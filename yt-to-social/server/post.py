"""X (Twitter) と Threads への投稿モジュール.

DRY_RUN=true のときはAPIを叩かず、stdout出力のみ行う (開発時の安全装置)。
"""

from __future__ import annotations

import ipaddress
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import tweepy

from .config import settings
from .threads_token import current_access_token


def _log(msg: str) -> None:
    print(f"[post] {msg}", file=sys.stderr, flush=True)


@dataclass
class PostResult:
    platform: str
    success: bool
    post_id: str = ""
    url: str = ""
    error: str = ""
    dry_run: bool = False
    hint: str = ""  # 解決のためのアクション (例: 権限再設定リンク)


# ----- X (Twitter) ----------------------------------------------------------


def _x_clients() -> tuple[tweepy.Client, tweepy.API]:
    auth = tweepy.OAuth1UserHandler(
        settings.x_api_key,
        settings.x_api_secret,
        settings.x_access_token,
        settings.x_access_token_secret,
    )
    api_v1 = tweepy.API(auth)  # メディアアップロード用 (v1.1)
    client_v2 = tweepy.Client(
        consumer_key=settings.x_api_key,
        consumer_secret=settings.x_api_secret,
        access_token=settings.x_access_token,
        access_token_secret=settings.x_access_token_secret,
    )
    return client_v2, api_v1


_X_PERMISSION_HINT = (
    "Xアプリの権限が Read のみになっている可能性があります。"
    "https://developer.x.com/ → 対象アプリ → User authentication settings で "
    "App permissions を「Read and write」に変更し、Access Token と Secret を"
    "再発行して .env を更新してください (既存トークンは旧スコープのままです)。"
)


def _classify_x_error(e: Exception) -> tuple[str, str]:
    """tweepyの例外から (短い和訳メッセージ, 対処ヒント) を返す."""
    msg = str(e)
    if isinstance(e, tweepy.Forbidden) or "403" in msg:
        if "oauth1 app permissions" in msg.lower():
            return ("Xアプリの権限不足 (Read のみ)", _X_PERMISSION_HINT)
        if "duplicate" in msg.lower():
            return ("同一内容を直近で投稿済み (Xの重複ブロック)", "本文を変更してください。")
        return (f"X APIから拒否されました (403): {msg}", _X_PERMISSION_HINT)
    if isinstance(e, tweepy.Unauthorized) or "401" in msg:
        return (
            "認証失敗 (401)",
            "X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET を確認してください。",
        )
    if isinstance(e, tweepy.TooManyRequests) or "429" in msg:
        return ("レート制限 (429)", "Free tierは1日あたりの投稿上限があります。時間を置いて再試行してください。")
    return (f"{type(e).__name__}: {msg}", "")


def post_to_x(text: str, image_path: Path | None = None) -> PostResult:
    if settings.dry_run:
        _log(f"DRY-RUN X text={text!r} image={image_path}")
        return PostResult(platform="x", success=True, dry_run=True)

    if not all(
        [settings.x_api_key, settings.x_api_secret, settings.x_access_token, settings.x_access_token_secret]
    ):
        return PostResult(
            platform="x",
            success=False,
            error="X API credentials missing in .env",
            hint="X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET を .env に設定してください。",
        )

    try:
        client_v2, api_v1 = _x_clients()
        media_ids = None
        if image_path and image_path.exists():
            try:
                media = api_v1.media_upload(filename=str(image_path))
                media_ids = [media.media_id]
            except Exception as me:
                # 画像アップロード失敗時はテキストのみで投稿を試みる
                short, hint = _classify_x_error(me)
                _log(f"media_upload failed (continuing as text-only): {short}")
                _log(traceback.format_exc())
        resp = client_v2.create_tweet(text=text, media_ids=media_ids)
        tweet_id = str(resp.data["id"])
        _log(f"X posted ok id={tweet_id}")
        return PostResult(
            platform="x",
            success=True,
            post_id=tweet_id,
            url=f"https://x.com/i/web/status/{tweet_id}",
        )
    except Exception as e:
        short, hint = _classify_x_error(e)
        _log(f"X post failed: {short}")
        _log(traceback.format_exc())
        return PostResult(platform="x", success=False, error=short, hint=hint)


def check_x_credentials() -> dict[str, object]:
    """X資格情報の事前チェック. 投稿せずに verify_credentials / get_me を叩く."""
    if not all(
        [settings.x_api_key, settings.x_api_secret, settings.x_access_token, settings.x_access_token_secret]
    ):
        return {"ok": False, "error": "X API credentials missing in .env"}
    try:
        client_v2, api_v1 = _x_clients()
        me = api_v1.verify_credentials()
        # アプリ権限スコープは header `x-access-level` で返る (read / read-write / read-write-directmessages)
        access_level = ""
        try:
            last = api_v1.last_response
            if last is not None:
                access_level = last.headers.get("x-access-level", "")
        except Exception:
            pass
        return {
            "ok": True,
            "screen_name": getattr(me, "screen_name", ""),
            "access_level": access_level,
            "writable": access_level.startswith("read-write"),
            "hint": "" if access_level.startswith("read-write") else _X_PERMISSION_HINT,
        }
    except Exception as e:
        short, hint = _classify_x_error(e)
        return {"ok": False, "error": short, "hint": hint}


# ----- Threads (Meta Graph API) ---------------------------------------------
#
# 2段階フロー:
#   1) コンテナ作成 (POST /{user-id}/threads)
#   2) 公開        (POST /{user-id}/threads_publish)
# 画像付きの場合は image_url が public にアクセス可能なURLである必要がある。
# ローカル画像は事前にどこかへホスティングするか、本ツールの簡易公開エンドポイント経由で
# トンネリング(ngrok等)するのが確実。MVPでは image_public_url を引数で受ける形にする。


THREADS_API = "https://graph.threads.net/v1.0"


_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_public_http_url(url: str) -> bool:
    """MetaのサーバからアクセスできるURLか判定. 失敗時は False を返す."""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in _LOCAL_HOSTNAMES or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return False
    except ValueError:
        pass  # ホスト名（DNS）はOK
    return True


_CATBOX_API = "https://catbox.moe/user/api.php"


def _upload_to_catbox(image_path: Path) -> str:
    """ローカル画像を catbox.moe にアップロードして公開URLを返す。失敗時は例外。"""
    suffix = image_path.suffix.lower() or ".png"
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    with image_path.open("rb") as f:
        files = {"fileToUpload": (image_path.name, f, mime)}
        data = {"reqtype": "fileupload"}
        with httpx.Client(timeout=60) as cli:
            r = cli.post(_CATBOX_API, data=data, files=files)
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox returned non-URL response: {url[:200]}")
    return url


def post_to_threads(
    text: str,
    image_public_url: str | None = None,
    image_path: Path | None = None,
) -> PostResult:
    if settings.dry_run:
        _log(f"DRY-RUN Threads text={text!r} image_url={image_public_url}")
        return PostResult(platform="threads", success=True, dry_run=True)

    access_token = current_access_token()  # 50日超なら自動リフレッシュされる
    if not (access_token and settings.threads_user_id):
        return PostResult(
            platform="threads",
            success=False,
            error="THREADS_ACCESS_TOKEN or THREADS_USER_ID missing in .env",
            hint="Threads Graph APIのトークンとユーザIDを.envに設定してください。",
        )

    local_image_skipped = False
    uploaded_via_catbox = False
    url_is_usable = bool(image_public_url) and _is_public_http_url(image_public_url)

    if not url_is_usable:
        if image_path and image_path.exists():
            reason = "local" if image_public_url else "no public URL provided"
            _log(f"Threads: image_url unusable ({reason}); attempting catbox.moe upload")
            try:
                public_url = _upload_to_catbox(image_path)
                _log(f"Threads: uploaded to catbox.moe -> {public_url}")
                image_public_url = public_url
                uploaded_via_catbox = True
            except Exception as ue:
                _log(f"Threads: catbox upload failed ({ue}); falling back to text-only")
                _log(traceback.format_exc())
                image_public_url = None
                local_image_skipped = True
        else:
            if image_public_url:
                _log(f"Threads: image_url is local ({image_public_url}); no local file to upload")
                local_image_skipped = True
            image_public_url = None

    try:
        with httpx.Client(timeout=60) as cli:
            create_params = {
                "access_token": access_token,
                "text": text,
            }
            if image_public_url:
                create_params["media_type"] = "IMAGE"
                create_params["image_url"] = image_public_url
            else:
                create_params["media_type"] = "TEXT"

            r = cli.post(
                f"{THREADS_API}/{settings.threads_user_id}/threads",
                data=create_params,
            )
            r.raise_for_status()
            container_id = r.json()["id"]

            # Threadsは数秒待ってから publish した方が安定 (Metaドキュメント推奨)
            time.sleep(2)

            r2 = cli.post(
                f"{THREADS_API}/{settings.threads_user_id}/threads_publish",
                data={
                    "access_token": access_token,
                    "creation_id": container_id,
                },
            )
            r2.raise_for_status()
            post_id = r2.json()["id"]
            _log(f"Threads posted ok id={post_id}")
            hint_msg = ""
            if local_image_skipped:
                hint_msg = (
                    "画像URLがローカル(127.0.0.1/localhost等)のためMetaから取得できず、"
                    "テキストのみで投稿しました。画像付き投稿には ngrok 等で公開URLを用意してください。"
                )
            elif uploaded_via_catbox:
                hint_msg = f"画像は catbox.moe に自動アップロードしました ({image_public_url})"
            return PostResult(
                platform="threads",
                success=True,
                post_id=str(post_id),
                url=f"https://www.threads.net/@me/post/{post_id}",
                hint=hint_msg,
            )
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        _log(f"Threads HTTP {e.response.status_code}: {body}")
        return PostResult(
            platform="threads",
            success=False,
            error=f"Threads API {e.response.status_code}: {body}",
            hint="image_urlが公開到達可能か(127.0.0.1ではNG)、トークンの有効期限を確認してください。",
        )
    except Exception as e:
        _log(f"Threads post failed: {e}")
        _log(traceback.format_exc())
        return PostResult(platform="threads", success=False, error=f"{type(e).__name__}: {e}")
