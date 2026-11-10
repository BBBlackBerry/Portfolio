"""固定YouTubeチャンネルの動画一覧と字幕を取得し、DBコーパスに取り込む."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import settings
from ..youtube import fetch_transcript, ytdlp_opts
from . import db


def _log(msg: str) -> None:
    print(f"[jobnews.channel] {msg}", file=sys.stderr, flush=True)


@dataclass
class ChannelEntry:
    video_id: str
    title: str
    url: str
    published_at: str | None


def _normalize_channel_url(url: str) -> str:
    """チャンネルURLを「動画一覧タブ」に正規化する."""
    u = url.strip().rstrip("/")
    if not u:
        raise RuntimeError(
            "JOB_NEWS_CHANNEL_URL が .env に未設定です。対象チャンネルのURLを設定してください。"
        )
    if u.endswith("/videos") or "watch?v=" in u or "/playlist" in u:
        return u
    return f"{u}/videos"


def _epoch_to_iso(ts: object) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def list_channel_videos(channel_url: str, limit: int) -> list[ChannelEntry]:
    """yt-dlp のフラット抽出でチャンネルの最新動画を列挙する (字幕はまだ取得しない)."""
    from yt_dlp import YoutubeDL

    opts = ytdlp_opts({"extract_flat": True, "playlistend": limit})
    target = _normalize_channel_url(channel_url)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)

    entries: list[ChannelEntry] = []
    for e in info.get("entries") or []:
        if not e:
            continue
        vid = e.get("id")
        # タブやプレイリストが混ざることがあるので動画IDの形だけ通す
        if not vid or len(vid) != 11:
            continue
        published = _epoch_to_iso(e.get("timestamp"))
        if not published and e.get("upload_date"):
            try:
                d = datetime.strptime(str(e["upload_date"]), "%Y%m%d")
                published = d.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                published = None
        entries.append(
            ChannelEntry(
                video_id=vid,
                title=e.get("title") or f"video_{vid}",
                url=f"https://youtube.com/watch?v={vid}",
                published_at=published,
            )
        )
    return entries


def refresh_corpus(*, force: bool = False) -> dict[str, int]:
    """チャンネルの動画をDBに取り込む.

    既にDBにある動画は字幕取得をスキップする (force=True で再取得)。
    戻り値: {"listed": 列挙数, "added": 新規取り込み数, "skipped": 既存スキップ数, "failed": 字幕失敗数}
    """
    entries = list_channel_videos(settings.jobnews_channel_url, settings.jobnews_max_videos)
    existing = set() if force else db.video_ids()

    added = skipped = failed = 0
    for entry in entries:
        if entry.video_id in existing:
            skipped += 1
            continue
        try:
            transcript, _lang = fetch_transcript(
                entry.video_id, preferred_lang=settings.default_language
            )
        except Exception as e:  # 字幕無効/取得失敗の動画はタイトルだけ保存
            _log(f"字幕取得失敗 {entry.video_id}: {e}")
            transcript = ""
            failed += 1
        db.upsert_video(
            video_id=entry.video_id,
            title=entry.title,
            url=entry.url,
            transcript=transcript,
            published_at=entry.published_at,
        )
        added += 1

    _log(f"listed={len(entries)} added={added} skipped={skipped} failed={failed}")
    return {
        "listed": len(entries),
        "added": added,
        "skipped": skipped,
        "failed": failed,
    }
