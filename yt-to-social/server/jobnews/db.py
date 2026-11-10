"""就活ニュースアプリの SQLite ストレージ.

2つのテーブルを持つ:
  - videos : 固定チャンネルの動画字幕コーパス (内容マッチングの母集団)
  - drafts : 生成済みの投稿下書き (ニュース × 動画 のペアごと)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    transcript    TEXT NOT NULL DEFAULT '',
    published_at  TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,
    news_title          TEXT NOT NULL,
    news_url            TEXT NOT NULL UNIQUE,
    news_published      TEXT,
    news_summary        TEXT NOT NULL DEFAULT '',
    matched_video_id    TEXT,
    matched_video_title TEXT,
    matched_video_url   TEXT,
    relevance_score     REAL NOT NULL DEFAULT 0,
    relevance_reason    TEXT NOT NULL DEFAULT '',
    newsworthiness_score REAL NOT NULL DEFAULT 0,
    draft_x             TEXT NOT NULL DEFAULT '',
    draft_threads       TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft'
);
"""

# 既存DB向けの軽量マイグレーション: {テーブル: {カラム: 定義}}
_MIGRATIONS: dict[str, dict[str, str]] = {
    "drafts": {"newsworthiness_score": "REAL NOT NULL DEFAULT 0"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    """既存DBに不足カラムがあれば ALTER TABLE で追加する."""
    for table, columns in _MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


@dataclass
class VideoRow:
    video_id: str
    title: str
    url: str
    transcript: str
    published_at: str | None
    fetched_at: str


@dataclass
class DraftRow:
    id: int
    created_at: str
    news_title: str
    news_url: str
    news_published: str | None
    news_summary: str
    matched_video_id: str | None
    matched_video_title: str | None
    matched_video_url: str | None
    relevance_score: float
    relevance_reason: str
    newsworthiness_score: float
    draft_x: str
    draft_threads: str
    status: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """設定パスの SQLite に接続する. スキーマは毎回 IF NOT EXISTS で保証."""
    settings.jobnews_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.jobnews_db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection():
        pass


# ----- videos ---------------------------------------------------------------


def upsert_video(
    video_id: str,
    title: str,
    url: str,
    transcript: str,
    published_at: str | None,
) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO videos (video_id, title, url, transcript, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title        = excluded.title,
                url          = excluded.url,
                transcript   = excluded.transcript,
                published_at = excluded.published_at,
                fetched_at   = excluded.fetched_at
            """,
            (video_id, title, url, transcript, published_at, _now()),
        )


def video_ids() -> set[str]:
    with connection() as conn:
        return {r["video_id"] for r in conn.execute("SELECT video_id FROM videos")}


def all_videos() -> list[VideoRow]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM videos ORDER BY COALESCE(published_at, fetched_at) DESC"
        ).fetchall()
    return [VideoRow(**dict(r)) for r in rows]


def count_videos() -> int:
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]


# ----- drafts ---------------------------------------------------------------


def draft_url_seen(news_url: str) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM drafts WHERE news_url = ?", (news_url,)
        ).fetchone()
    return row is not None


def insert_draft(
    *,
    news_title: str,
    news_url: str,
    news_published: str | None,
    news_summary: str,
    matched_video_id: str | None,
    matched_video_title: str | None,
    matched_video_url: str | None,
    relevance_score: float,
    relevance_reason: str,
    newsworthiness_score: float,
    draft_x: str,
    draft_threads: str,
) -> int:
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO drafts (
                created_at, news_title, news_url, news_published, news_summary,
                matched_video_id, matched_video_title, matched_video_url,
                relevance_score, relevance_reason, newsworthiness_score,
                draft_x, draft_threads, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            ON CONFLICT(news_url) DO UPDATE SET
                created_at           = excluded.created_at,
                news_title           = excluded.news_title,
                news_summary         = excluded.news_summary,
                matched_video_id     = excluded.matched_video_id,
                matched_video_title  = excluded.matched_video_title,
                matched_video_url    = excluded.matched_video_url,
                relevance_score      = excluded.relevance_score,
                relevance_reason     = excluded.relevance_reason,
                newsworthiness_score = excluded.newsworthiness_score,
                draft_x              = excluded.draft_x,
                draft_threads        = excluded.draft_threads
            """,
            (
                _now(), news_title, news_url, news_published, news_summary,
                matched_video_id, matched_video_title, matched_video_url,
                relevance_score, relevance_reason, newsworthiness_score,
                draft_x, draft_threads,
            ),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = conn.execute(
            "SELECT id FROM drafts WHERE news_url = ?", (news_url,)
        ).fetchone()
        return int(row["id"])


def list_drafts(include_archived: bool = False) -> list[DraftRow]:
    with connection() as conn:
        # 関連動画ありの下書きも、関連動画なしの「人気ニュースまとめ」も拾えるよう
        # 関連度と注目度の高い方でソートする。
        order = "ORDER BY MAX(relevance_score, newsworthiness_score) DESC, created_at DESC"
        if include_archived:
            rows = conn.execute(f"SELECT * FROM drafts {order}").fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM drafts WHERE status != 'archived' {order}"
            ).fetchall()
    return [DraftRow(**dict(r)) for r in rows]


def update_draft_status(draft_id: int, status: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE drafts SET status = ? WHERE id = ?", (status, draft_id)
        )
