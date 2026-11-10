"""FastAPI サーバー — プレビューUI と 投稿エンドポイント."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .generate import generate_posts
from .image import generate_image
from .post import check_x_credentials, post_to_threads, post_to_x
from .youtube import get_video

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="yt-to-social")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/output", StaticFiles(directory=settings.output_dir), name="output")


# ----- Schemas --------------------------------------------------------------


class GenerateRequest(BaseModel):
    url: str
    generate_images: bool = True
    posts_per_url: int = Field(default=1, ge=1, le=3)


class PostDraftDTO(BaseModel):
    pattern: str
    psychological_trigger: str
    text: str
    image_prompt: str
    image_caption_jp: str = ""
    image_badge_jp: str = ""
    image_headline_jp: str = ""
    image_subhead_jp: str = ""
    image_bullets_jp: list[str] = Field(default_factory=list)
    image_footer_jp: str = ""
    image_url: str = ""  # /output/xxx.png 形式
    image_path: str = ""  # 絶対パス (投稿時に使用)


class GenerateResponse(BaseModel):
    video_id: str
    video_title: str
    video_url: str
    thumbnail_url: str
    language: str
    transcript_excerpt: str
    x_posts: list[PostDraftDTO]
    threads_posts: list[PostDraftDTO]


class PostRequest(BaseModel):
    platform: Literal["x", "threads"]
    text: str
    image_path: str = ""
    image_public_url: str = ""  # Threads用


class PostResponse(BaseModel):
    success: bool
    platform: str
    post_id: str = ""
    url: str = ""
    error: str = ""
    hint: str = ""
    dry_run: bool = False


class RegenerateImageRequest(BaseModel):
    image_prompt: str
    image_badge_jp: str = ""
    image_headline_jp: str = ""
    image_subhead_jp: str = ""
    image_bullets_jp: list[str] = Field(default_factory=list)
    image_footer_jp: str = ""


class RegenerateImageResponse(BaseModel):
    image_url: str
    image_path: str


# ----- Routes ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "dry_run": settings.dry_run,
        "text_provider": settings.text_provider,
        "claude_model": settings.claude_model,
        "gemini_text_model": settings.gemini_text_model,
        "image_model": settings.image_model,
        "anthropic_key_set": bool(settings.anthropic_api_key),
        "google_key_set": bool(settings.google_api_key),
        "x_keys_set": all(
            [
                settings.x_api_key,
                settings.x_api_secret,
                settings.x_access_token,
                settings.x_access_token_secret,
            ]
        ),
        "threads_keys_set": bool(settings.threads_access_token and settings.threads_user_id),
    }


@app.post("/api/generate", response_model=GenerateResponse)
def api_generate(req: GenerateRequest) -> GenerateResponse:
    try:
        video = get_video(req.url, preferred_lang=settings.default_language)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YouTube取得失敗: {e}") from e

    try:
        gen = generate_posts(
            video_title=video.title,
            video_url=video.url,
            transcript=video.transcript,
            posts_per_url=req.posts_per_url,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"投稿文生成失敗 (provider={settings.text_provider}): {e}",
        ) from e

    # 1動画につき画像は1枚だけ生成し、X / Threads の全投稿で共有する。
    # 画像プロンプトの素材は最初に見つかった draft (通常は x_posts[0]) から取る。
    shared_image_path = ""
    shared_image_url = ""
    image_error: str | None = None
    if req.generate_images:
        source = next(iter(gen.x_posts), None) or next(iter(gen.threads_posts), None)
        if source is not None:
            try:
                p = generate_image(
                    source.image_prompt,
                    badge_jp=source.image_badge_jp,
                    headline_jp=source.image_headline_jp,
                    subhead_jp=source.image_subhead_jp,
                    bullets_jp=source.image_bullets_jp,
                    footer_jp=source.image_footer_jp,
                )
                shared_image_path = str(p)
                shared_image_url = f"/output/{p.name}"
            except Exception as e:
                image_error = str(e)

    def to_dto(drafts) -> list[PostDraftDTO]:
        out: list[PostDraftDTO] = []
        for d in drafts:
            dto = PostDraftDTO(**d.model_dump())
            if shared_image_path:
                dto.image_path = shared_image_path
                dto.image_url = shared_image_url
            elif image_error is not None:
                dto.image_prompt = f"[image generation failed: {image_error}] {d.image_prompt}"
            out.append(dto)
        return out

    return GenerateResponse(
        video_id=video.video_id,
        video_title=video.title,
        video_url=video.url,
        thumbnail_url=video.thumbnail_url,
        language=video.language,
        transcript_excerpt=video.transcript[:300] + ("..." if len(video.transcript) > 300 else ""),
        x_posts=to_dto(gen.x_posts),
        threads_posts=to_dto(gen.threads_posts),
    )


@app.post("/api/post", response_model=PostResponse)
def api_post(req: PostRequest) -> PostResponse:
    img_path = Path(req.image_path) if req.image_path else None
    if req.platform == "x":
        r = post_to_x(req.text, img_path)
    else:
        r = post_to_threads(req.text, req.image_public_url or None, img_path)
    return PostResponse(
        success=r.success,
        platform=r.platform,
        post_id=r.post_id,
        url=r.url,
        error=r.error,
        hint=r.hint,
        dry_run=r.dry_run,
    )


@app.post("/api/regenerate-image", response_model=RegenerateImageResponse)
def api_regenerate_image(req: RegenerateImageRequest) -> RegenerateImageResponse:
    try:
        p = generate_image(
            req.image_prompt,
            badge_jp=req.image_badge_jp,
            headline_jp=req.image_headline_jp,
            subhead_jp=req.image_subhead_jp,
            bullets_jp=req.image_bullets_jp,
            footer_jp=req.image_footer_jp,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"画像再生成失敗: {e}") from e
    return RegenerateImageResponse(image_path=str(p), image_url=f"/output/{p.name}")


@app.get("/api/x/check")
def api_x_check() -> dict[str, object]:
    """投稿せずにX資格情報・権限を検査する。"""
    return check_x_credentials()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "server.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
