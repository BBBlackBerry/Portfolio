"""就活ニュース自動収集アプリの FastAPI サーバー — 下書き生成UIと投稿エンドポイント."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import settings
from ..post import post_to_threads, post_to_x
from . import db
from .pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="jobnews — 就活ニュース下書き生成")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ----- Schemas --------------------------------------------------------------


class RunRequest(BaseModel):
    refresh_channel: bool = False


class DraftDTO(BaseModel):
    id: int
    created_at: str
    news_title: str
    news_url: str
    news_published: str | None = None
    news_summary: str
    matched_video_id: str | None = None
    matched_video_title: str | None = None
    matched_video_url: str | None = None
    relevance_score: float
    relevance_reason: str
    newsworthiness_score: float = 0.0
    draft_x: str
    draft_threads: str
    status: str


class RunResponse(BaseModel):
    corpus: dict[str, int]
    news_found: int
    news_analyzed: int
    drafts_created: int
    summaries_created: int
    skipped_already_seen: int
    errors: list[str]
    drafts: list[DraftDTO]


class PostRequest(BaseModel):
    draft_id: int
    platform: Literal["x", "threads"]
    text: str


class PostResponse(BaseModel):
    success: bool
    platform: str
    post_id: str = ""
    url: str = ""
    error: str = ""
    hint: str = ""
    dry_run: bool = False


class StatusRequest(BaseModel):
    draft_id: int
    status: Literal["draft", "posted", "archived"]


# ----- Helpers --------------------------------------------------------------


def _to_dto(row: db.DraftRow) -> DraftDTO:
    return DraftDTO(**row.__dict__)


# ----- Routes ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "jobnews.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "dry_run": settings.dry_run,
        "claude_model": settings.claude_model,
        "channel_url_set": bool(settings.jobnews_channel_url),
        "news_provider": "duckduckgo",
        "anthropic_key_set": bool(settings.anthropic_api_key),
        "x_keys_set": all(
            [
                settings.x_api_key,
                settings.x_api_secret,
                settings.x_access_token,
                settings.x_access_token_secret,
            ]
        ),
        "threads_keys_set": bool(
            settings.threads_access_token and settings.threads_user_id
        ),
        "corpus_videos": db.count_videos(),
        "search_queries": list(settings.jobnews_search_queries),
        "relevance_threshold": settings.jobnews_relevance_threshold,
    }


@app.get("/api/drafts", response_model=list[DraftDTO])
def api_drafts(include_archived: bool = False) -> list[DraftDTO]:
    return [_to_dto(r) for r in db.list_drafts(include_archived=include_archived)]


@app.post("/api/run", response_model=RunResponse)
def api_run(req: RunRequest) -> RunResponse:
    try:
        result = run_pipeline(refresh_channel=req.refresh_channel)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"パイプライン実行失敗: {e}") from e
    return RunResponse(
        corpus=result.corpus,
        news_found=result.news_found,
        news_analyzed=result.news_analyzed,
        drafts_created=result.drafts_created,
        summaries_created=result.summaries_created,
        skipped_already_seen=result.skipped_already_seen,
        errors=result.errors,
        drafts=[_to_dto(r) for r in db.list_drafts()],
    )


@app.post("/api/post", response_model=PostResponse)
def api_post(req: PostRequest) -> PostResponse:
    if req.platform == "x":
        r = post_to_x(req.text, None)
    else:
        r = post_to_threads(req.text, None)
    if r.success and not r.dry_run:
        db.update_draft_status(req.draft_id, "posted")
    return PostResponse(
        success=r.success,
        platform=r.platform,
        post_id=r.post_id,
        url=r.url,
        error=r.error,
        hint=r.hint,
        dry_run=r.dry_run,
    )


@app.post("/api/draft/status")
def api_draft_status(req: StatusRequest) -> dict[str, object]:
    db.update_draft_status(req.draft_id, req.status)
    return {"ok": True, "draft_id": req.draft_id, "status": req.status}


def main() -> None:
    import uvicorn

    db.init_db()
    uvicorn.run(
        "server.jobnews.app:app",
        host=settings.app_host,
        port=settings.jobnews_app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
