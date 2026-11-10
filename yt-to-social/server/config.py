"""環境変数の読み込みと型付き設定オブジェクト."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)


@dataclass(frozen=True)
class Settings:
    # Anthropic
    anthropic_api_key: str
    claude_model: str

    # 投稿文生成 (テキスト)
    text_provider: str       # "gemini" | "claude"
    gemini_text_model: str   # text_provider="gemini" のとき使うモデル

    # 画像生成 (Google Gemini)
    google_api_key: str
    image_model: str

    # X
    x_api_key: str
    x_api_secret: str
    x_access_token: str
    x_access_token_secret: str

    # Threads
    threads_app_id: str
    threads_app_secret: str
    threads_access_token: str
    threads_user_id: str

    # YouTube (任意)
    youtube_api_key: str

    # 就活ニュース自動収集アプリ
    jobnews_channel_url: str
    jobnews_app_port: int
    jobnews_db_path: Path
    jobnews_max_videos: int
    jobnews_search_queries: tuple[str, ...]
    jobnews_relevance_threshold: float
    jobnews_date_restrict: str

    # アプリ
    app_port: int
    app_host: str
    output_dir: Path
    default_language: str
    dry_run: bool


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def load_settings() -> Settings:
    output_dir = Path(_env("OUTPUT_DIR", "./output"))
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    jobnews_db_path = Path(_env("JOBNEWS_DB_PATH", "./output/jobnews.db"))
    if not jobnews_db_path.is_absolute():
        jobnews_db_path = ROOT / jobnews_db_path

    raw_queries = _env(
        "JOBNEWS_SEARCH_QUERIES",
        "就活 ニュース,新卒採用 2026,インターンシップ 採用,就活 トレンド",
    )
    jobnews_search_queries = tuple(q.strip() for q in raw_queries.split(",") if q.strip())

    return Settings(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        claude_model=_env("CLAUDE_MODEL", "claude-sonnet-4-6"),
        text_provider=_env("TEXT_PROVIDER", "gemini").lower(),
        gemini_text_model=_env("GEMINI_TEXT_MODEL", "gemini-2.5-pro"),
        google_api_key=_env("GOOGLE_API_KEY"),
        image_model=_env("IMAGE_MODEL", "gemini-2.5-flash-image"),
        x_api_key=_env("X_API_KEY"),
        x_api_secret=_env("X_API_SECRET"),
        x_access_token=_env("X_ACCESS_TOKEN"),
        x_access_token_secret=_env("X_ACCESS_TOKEN_SECRET"),
        threads_app_id=_env("THREADS_APP_ID"),
        threads_app_secret=_env("THREADS_APP_SECRET"),
        threads_access_token=_env("THREADS_ACCESS_TOKEN"),
        threads_user_id=_env("THREADS_USER_ID"),
        youtube_api_key=_env("YOUTUBE_API_KEY"),
        jobnews_channel_url=_env("JOB_NEWS_CHANNEL_URL"),
        jobnews_app_port=int(_env("JOBNEWS_APP_PORT", "8001")),
        jobnews_db_path=jobnews_db_path,
        jobnews_max_videos=int(_env("JOBNEWS_MAX_VIDEOS", "30")),
        jobnews_search_queries=jobnews_search_queries,
        jobnews_relevance_threshold=float(_env("JOBNEWS_RELEVANCE_THRESHOLD", "0.3")),
        jobnews_date_restrict=_env("JOBNEWS_DATE_RESTRICT", "w4"),
        app_port=int(_env("APP_PORT", "8000")),
        app_host=_env("APP_HOST", "127.0.0.1"),
        output_dir=output_dir,
        default_language=_env("DEFAULT_LANGUAGE", "ja"),
        dry_run=_env("DRY_RUN", "true").lower() == "true",
    )


settings = load_settings()
