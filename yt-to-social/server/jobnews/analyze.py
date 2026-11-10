"""Claude による就活ニュースの関連度判定 + 投稿下書き生成 (1記事 = 1 API呼び出し).

`.claude/skills/generate-jobnews-draft/SKILL.md` のブランド戦略をシステムプロンプトに、
チャンネル動画コーパスをキャッシュ済みコンテキストに載せ、ニュース記事ごとに
「最も関連する動画」「関連度スコア」「X / Threads 下書き」をまとめて返させる。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

from ..config import settings
from ..generate import _extract_json  # JSON抽出ロジックを再利用
from .db import VideoRow
from .news import NewsItem

ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_PATH = ROOT / ".claude" / "skills" / "generate-jobnews-draft" / "SKILL.md"

_TRANSCRIPT_EXCERPT_CHARS = 700
_client: Anthropic | None = None


def _log(msg: str) -> None:
    print(f"[jobnews.analyze] {msg}", file=sys.stderr, flush=True)


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY が .env に未設定です")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _load_skill() -> str:
    if not SKILL_PATH.exists():
        raise RuntimeError(f"Skill定義が見つかりません: {SKILL_PATH}")
    return SKILL_PATH.read_text(encoding="utf-8")


def _corpus_text(videos: list[VideoRow]) -> str:
    """動画コーパスを Claude に渡す軽量テキストに整形する."""
    lines: list[str] = []
    for v in videos:
        excerpt = v.transcript[:_TRANSCRIPT_EXCERPT_CHARS].replace("\n", " ")
        lines.append(
            json.dumps(
                {
                    "video_id": v.video_id,
                    "title": v.title,
                    "url": v.url,
                    "transcript_excerpt": excerpt,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


@dataclass
class AnalysisResult:
    matched_video_id: str | None
    matched_video_title: str | None
    matched_video_url: str | None
    relevance_score: float
    relevance_reason: str
    newsworthiness_score: float
    news_summary: str
    draft_x: str
    draft_threads: str


_OUTPUT_INSTRUCTION = (
    "次のJSON Schemaに厳密に従って出力してください。前置き・後書き・コードフェンスは不要です。\n"
    "{\n"
    '  "matched_video_id": "最も関連する動画のvideo_id (関連動画が無ければ null)",\n'
    '  "relevance_score": 0.0〜1.0 のニュースと動画の関連度,\n'
    '  "relevance_reason": "なぜ関連する/しないと判断したかを1〜2文で",\n'
    '  "newsworthiness_score": 0.0〜1.0。ニュース自体の注目度・話題性。'
    "関連動画の有無とは独立で、チャンネルの視聴者にとって共有する価値が高いほど高くする,\n"
    '  "news_summary": "ニュース記事の要点をブランドトーンで2〜3文に要約",\n'
    '  "draft_x": "X向け投稿下書き。関連動画があれば動画ブリッジ付き、無ければ単独のニュース紹介投稿として生成。'
    '必ず生成し、空文字は禁止",\n'
    '  "draft_threads": "Threads向け投稿下書き。draft_x と同じ方針で生成。必ず生成し、空文字は禁止"\n'
    "}"
)


def analyze_news(news: NewsItem, videos: list[VideoRow]) -> AnalysisResult:
    """1件のニュースを動画コーパスと突き合わせ、関連度と下書きを生成する."""
    client = _get_client()
    skill_md = _load_skill()
    corpus = _corpus_text(videos)

    news_payload = json.dumps(
        {
            "title": news.title,
            "url": news.url,
            "published": news.published,
            "snippet": news.snippet,
            "content": news.content[:6000],
        },
        ensure_ascii=False,
    )

    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=4000,
        system=[
            {
                "type": "text",
                "text": skill_md,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "# チャンネル動画コーパス (1行1動画のJSON)\n" + corpus
                        ),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "上のコーパスは固定YouTubeチャンネルの動画一覧です。\n"
                            "下の就活ニュース記事について、どの動画の内容と最も関連するかを判定し、\n"
                            "Skill の戦略に従って X / Threads の投稿下書きを必ず生成してください。\n"
                            "- 関連動画があれば動画ブリッジ付きの下書きにしてください。\n"
                            "- 関連動画が無くても、ニュース単独で投稿できる下書きを生成してください。\n"
                            "- relevance_score / newsworthiness_score が低くても draft_x / draft_threads は空にせず、必ず生成してください。\n"
                            "- news_summary も必ず記入してください。\n\n"
                            f"# 就活ニュース記事\n{news_payload}\n\n"
                            f"# 出力形式\n{_OUTPUT_INSTRUCTION}"
                        ),
                    },
                ],
            }
        ],
    )

    if getattr(msg, "stop_reason", None) == "max_tokens":
        _log(f"max_tokensで打ち切り: {news.url}")

    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    data = _extract_json(raw)

    mvid = data.get("matched_video_id") or None
    video_by_id = {v.video_id: v for v in videos}
    matched = video_by_id.get(mvid) if mvid else None

    def _clamped(key: str) -> float:
        try:
            v = float(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, v))

    return AnalysisResult(
        matched_video_id=matched.video_id if matched else None,
        matched_video_title=matched.title if matched else None,
        matched_video_url=matched.url if matched else None,
        relevance_score=_clamped("relevance_score"),
        relevance_reason=str(data.get("relevance_reason", "")).strip(),
        newsworthiness_score=_clamped("newsworthiness_score"),
        news_summary=str(data.get("news_summary", "")).strip(),
        draft_x=str(data.get("draft_x", "")).strip(),
        draft_threads=str(data.get("draft_threads", "")).strip(),
    )
