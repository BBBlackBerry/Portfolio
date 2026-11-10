"""Threads long-lived access token のキャッシュ & 自動リフレッシュ.

長期トークンは60日 (5,184,000秒) で失効する。
24時間以上経過していれば th_refresh_token で再発行でき、再発行すると60日カウンタがリセットされる。

このモジュールは:
  - 現在のトークンと発行時刻を output/threads_token.json に保存
  - current_access_token() で取得する際、50日以上経っていたら自動的にリフレッシュ
  - 初回は .env の THREADS_ACCESS_TOKEN をシードとして使う

cron 等から明示的にリフレッシュしたい場合は scripts/refresh_threads_token.py を実行する。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import settings

THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"
REFRESH_AFTER_SECONDS = 50 * 24 * 60 * 60  # 50日経ったらリフレッシュ
MIN_AGE_FOR_REFRESH = 25 * 60 * 60         # API側は24h以上必要 (1h余裕)

_TOKEN_PATH = settings.output_dir / "threads_token.json"
_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[threads_token] {msg}", file=sys.stderr, flush=True)


@dataclass
class TokenState:
    access_token: str
    issued_at: float        # unix秒
    expires_in: int = 5184000

    def age(self) -> float:
        return time.time() - self.issued_at

    def remaining(self) -> float:
        return self.expires_in - self.age()


def _load() -> TokenState | None:
    if not _TOKEN_PATH.exists():
        return None
    try:
        d = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
        return TokenState(
            access_token=d["access_token"],
            issued_at=float(d["issued_at"]),
            expires_in=int(d.get("expires_in", 5184000)),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        _log(f"sidecar 読み込み失敗 ({e}) — .env から再シードします")
        return None


def _save(state: TokenState) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(
        json.dumps(
            {
                "access_token": state.access_token,
                "issued_at": state.issued_at,
                "expires_in": state.expires_in,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _seed_from_env() -> TokenState | None:
    if not settings.threads_access_token:
        return None
    state = TokenState(
        access_token=settings.threads_access_token,
        issued_at=time.time(),  # 初回は「今」発行されたとみなす (保守的に近い側へ寄せる)
    )
    _save(state)
    _log(f".env からシード (issued_at=now, path={_TOKEN_PATH})")
    return state


def _refresh(state: TokenState) -> TokenState:
    """Threads API を叩いて新しい長期トークンを取得する."""
    _log(f"リフレッシュ実行 (現トークン age={state.age()/86400:.1f}日)")
    with httpx.Client(timeout=30) as cli:
        r = cli.get(
            THREADS_REFRESH_URL,
            params={
                "grant_type": "th_refresh_token",
                "access_token": state.access_token,
            },
        )
        r.raise_for_status()
        data = r.json()
    new_state = TokenState(
        access_token=data["access_token"],
        issued_at=time.time(),
        expires_in=int(data.get("expires_in", 5184000)),
    )
    _save(new_state)
    _log(f"リフレッシュ成功 (expires_in={new_state.expires_in}s ≈ {new_state.expires_in/86400:.0f}日)")
    return new_state


def current_access_token() -> str:
    """現在使うべき Threads アクセストークンを返す.

    50日以上経過していて、かつ最低更新可能経過時間 (24h+α) を満たしていれば
    自動的にリフレッシュする。リフレッシュに失敗した場合は古いトークンを返す
    (まだ有効期限内である可能性が高いため呼び出し側で実行は試みる)。
    """
    with _lock:
        state = _load() or _seed_from_env()
        if state is None:
            return ""

        if state.age() >= REFRESH_AFTER_SECONDS and state.age() >= MIN_AGE_FOR_REFRESH:
            try:
                state = _refresh(state)
            except Exception as e:  # noqa: BLE001 — 通信/API/JSON 何でも握りつぶす
                _log(f"自動リフレッシュ失敗 ({e}) — 既存トークンで継続")

        return state.access_token


def refresh_now(force: bool = True) -> TokenState:
    """明示的にリフレッシュする (cron / CLI 用).

    force=False のときは MIN_AGE_FOR_REFRESH を満たさない場合スキップする。
    """
    with _lock:
        state = _load() or _seed_from_env()
        if state is None:
            raise RuntimeError("THREADS_ACCESS_TOKEN が .env に未設定です")
        if not force and state.age() < MIN_AGE_FOR_REFRESH:
            _log(f"スキップ (age={state.age()/3600:.1f}h < 必要 {MIN_AGE_FOR_REFRESH/3600:.0f}h)")
            return state
        return _refresh(state)
