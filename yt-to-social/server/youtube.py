"""YouTube動画から字幕とメタデータを取得する.

取得経路:
1. yt-dlp で字幕トラックを取得 (主)
2. youtube-transcript-api で取得 (フォールバック)
3. 一度取得した字幕は output/transcript_cache/ にキャッシュして再利用する
"""

from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from .config import settings  # import 副作用で .env が読み込まれる


@dataclass
class VideoData:
    video_id: str
    title: str
    url: str
    transcript: str
    language: str
    thumbnail_url: str


def extract_video_id(url_or_id: str) -> str:
    """URLまたはIDから video_id を抽出."""
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s

    parsed = urlparse(s)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.lstrip("/")
    if "youtube.com" in parsed.netloc:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        m = re.match(r"^/(shorts|embed|live)/([A-Za-z0-9_-]{11})", parsed.path)
        if m:
            return m.group(2)
    raise ValueError(f"YouTube URLからvideo_idを抽出できませんでした: {url_or_id}")


# ── yt-dlp オプション ────────────────────────────────────────────


def ytdlp_opts(extra: dict | None = None) -> dict:
    """yt-dlp 共通オプション."""
    opts: dict = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
    }
    if extra:
        opts.update(extra)
    return opts


# ── 字幕キャッシュ (output/transcript_cache/<video_id>.json) ──────


def _cache_path(video_id: str) -> Path:
    d = settings.output_dir / "transcript_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{video_id}.json"


def _cache_get(video_id: str) -> tuple[str, str] | None:
    p = _cache_path(video_id)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    text = obj.get("transcript") or ""
    return (text, obj.get("language", "")) if text else None


def _cache_put(video_id: str, transcript: str, language: str) -> None:
    if not transcript:
        return
    try:
        _cache_path(video_id).write_text(
            json.dumps({"transcript": transcript, "language": language}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[youtube] 字幕キャッシュ書き込み失敗 {video_id}: {e}", file=sys.stderr)


# ── 字幕パーサ ───────────────────────────────────────────────────


def _parse_json3(data: str) -> str:
    """YouTube timedtext の json3 形式を平文に変換."""
    obj = json.loads(data)
    parts: list[str] = []
    for event in obj.get("events", []):
        for seg in event.get("segs") or []:
            t = seg.get("utf8", "")
            if t:
                parts.append(t)
    return "".join(parts)


def _parse_srv1(data: str) -> str:
    """YouTube timedtext の srv1 (XML) 形式を平文に変換."""
    texts = re.findall(r"<text[^>]*>(.*?)</text>", data, re.DOTALL)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", t)) for t in texts)


def _parse_vtt(data: str) -> str:
    """WebVTT を平文に変換 (連続する重複行は畳む)."""
    out: list[str] = []
    for line in data.splitlines():
        line = line.strip()
        if (
            not line
            or line == "WEBVTT"
            or "-->" in line
            or line.isdigit()
            or line.startswith(("Kind:", "Language:", "NOTE"))
        ):
            continue
        line = html.unescape(re.sub(r"<[^>]+>", "", line))
        if line and (not out or out[-1] != line):
            out.append(line)
    return " ".join(out)


# ── yt-dlp 経由の字幕取得 ────────────────────────────────────────


def _pick_caption_lang(source: dict, preferred_lang: str) -> str | None:
    """字幕トラック辞書から使う言語キーを選ぶ (完全一致 → プレフィックス一致 → 先頭)."""
    if not source:
        return None
    lang_pref: list[str] = []
    for lang in (preferred_lang, "en", "ja"):
        if lang and lang not in lang_pref:
            lang_pref.append(lang)
    for lang in lang_pref:
        if lang in source:
            return lang
        for key in source:
            if key.startswith(lang + "-"):  # 例: "ja" → "ja-orig"
                return key
    return next(iter(source))


def _transcript_from_info(ydl, info: dict, preferred_lang: str) -> tuple[str, str]:
    """yt-dlp の info dict から字幕トラックを選び、ダウンロードして整形する."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    # 手動字幕を優先、無ければ自動字幕
    for source in (manual, auto):
        lang = _pick_caption_lang(source, preferred_lang)
        if not lang:
            continue
        tracks = source.get(lang) or []
        # パースしやすい json3 を最優先
        track = (
            next((t for t in tracks if t.get("ext") == "json3"), None)
            or next((t for t in tracks if t.get("ext") == "srv1"), None)
            or next((t for t in tracks if t.get("ext") == "vtt"), None)
            or (tracks[0] if tracks else None)
        )
        if not track or not track.get("url"):
            continue

        raw = ydl.urlopen(track["url"]).read().decode("utf-8", "replace")
        ext = track.get("ext")
        if ext == "json3":
            text = _parse_json3(raw)
        elif ext == "srv1":
            text = _parse_srv1(raw)
        else:
            text = _parse_vtt(raw)

        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text, lang

    raise RuntimeError("yt-dlp: 字幕トラックが見つかりませんでした")


def _meta_from_info(info: dict, video_id: str) -> tuple[str, str]:
    title = info.get("title") or f"video_{video_id}"
    thumb = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return title, thumb


# ── 公開API ─────────────────────────────────────────────────────


def fetch_transcript(video_id: str, preferred_lang: str = "ja") -> tuple[str, str]:
    """字幕を結合した文字列と、実際に取れた言語コードを返す.

    キャッシュ → yt-dlp → youtube-transcript-api の順に試す。
    成功した字幕はキャッシュに保存する。
    """
    cached = _cache_get(video_id)
    if cached:
        return cached

    errors: list[str] = []

    # 1) yt-dlp 主経路
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(ytdlp_opts()) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            text, lang = _transcript_from_info(ydl, info, preferred_lang)
        _cache_put(video_id, text, lang)
        return text, lang
    except Exception as e:
        errors.append(f"yt-dlp: {e}")

    # 2) youtube-transcript-api フォールバック
    try:
        api = YouTubeTranscriptApi()
        try:
            entries = api.fetch(video_id, languages=[preferred_lang]).snippets
            lang = preferred_lang
        except NoTranscriptFound:
            entries = api.fetch(video_id, languages=["en", "ja"]).snippets
            lang = "auto"
        text = " ".join(getattr(e, "text", "") for e in entries)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            _cache_put(video_id, text, lang)
            return text, lang
        errors.append("youtube-transcript-api: 空の字幕")
    except TranscriptsDisabled as e:
        raise RuntimeError(f"この動画は字幕が無効化されています: {video_id}") from e
    except Exception as e:
        errors.append(f"youtube-transcript-api: {e}")

    detail = " / ".join(errors)
    raise RuntimeError(f"字幕取得に失敗しました ({video_id}): {detail}")


def fetch_metadata(video_id: str) -> tuple[str, str]:
    """yt-dlp でタイトルとサムネイルURLを取得 (APIキー不要)."""
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(ytdlp_opts()) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
        return _meta_from_info(info, video_id)
    except Exception:
        return f"video_{video_id}", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def get_video(url_or_id: str, preferred_lang: str = "ja") -> VideoData:
    video_id = extract_video_id(url_or_id)

    # キャッシュ済みなら字幕は再取得しない (メタデータだけ取りに行く)
    cached = _cache_get(video_id)
    if cached:
        transcript, lang = cached
        title, thumb = fetch_metadata(video_id)
        return VideoData(
            video_id=video_id,
            title=title,
            url=f"https://youtube.com/watch?v={video_id}",
            transcript=transcript,
            language=lang,
            thumbnail_url=thumb,
        )

    # 1回の yt-dlp 呼び出しで字幕とメタデータを両方取得する
    transcript: str | None = None
    lang = ""
    title = thumb = ""
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(ytdlp_opts()) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            title, thumb = _meta_from_info(info, video_id)
            try:
                transcript, lang = _transcript_from_info(ydl, info, preferred_lang)
            except Exception:
                transcript = None  # 字幕だけ下のフォールバックに回す
    except Exception:
        pass

    if transcript is None:
        # yt-dlp 経路が失敗 → 完全なフォールバックチェーンを再試行
        transcript, lang = fetch_transcript(video_id, preferred_lang)
    else:
        _cache_put(video_id, transcript, lang)
    if not title:
        title, thumb = fetch_metadata(video_id)

    return VideoData(
        video_id=video_id,
        title=title,
        url=f"https://youtube.com/watch?v={video_id}",
        transcript=transcript,
        language=lang,
        thumbnail_url=thumb,
    )
