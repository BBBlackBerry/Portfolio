"""字幕から X / Threads 投稿文を LLM で生成する.

`.claude/skills/generate-social-posts/SKILL.md` の戦略をシステムプロンプトとして読み込み、
TEXT_PROVIDER に応じて Anthropic Claude / Google Gemini のいずれかに投げる。

Provider:
  - "gemini": google-genai SDK + structured output (response_schema) で JSON を直接受け取る
  - "claude": Anthropic Messages API + プロンプトキャッシュ (cache_control)

就活ニュース (server.jobnews.*) は常に Claude を使うため、この切替の影響を受けない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import settings

ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / ".claude" / "skills" / "generate-social-posts" / "SKILL.md"


class PostDraft(BaseModel):
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


class GeneratedPosts(BaseModel):
    x_posts: list[PostDraft] = Field(default_factory=list)
    threads_posts: list[PostDraft] = Field(default_factory=list)


def _load_skill() -> str:
    if not SKILL_PATH.exists():
        raise RuntimeError(f"Skill定義が見つかりません: {SKILL_PATH}")
    return SKILL_PATH.read_text(encoding="utf-8")


# ----- JSON 抽出ユーティリティ (Claude 経路のフォールバック用) ---------------


def _strip_code_fence(text: str) -> str:
    """モデルがJSONをコードフェンスで囲んだ場合に中身だけを返す。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    parts = s.split("```", 2)
    if len(parts) < 3:
        return s
    s = parts[1]
    if s.lstrip().startswith("json"):
        s = s.lstrip()[4:]
    return s.rsplit("```", 1)[0].strip()


def _find_json_start(text: str) -> int:
    candidates = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
    return min(candidates) if candidates else -1


def _extract_json_block(text: str) -> str:
    """文字列から最初のトップレベルJSONブロックを抽出し、不完全ならそのまま返す。"""
    s = _strip_code_fence(text)
    start = _find_json_start(s)
    if start == -1:
        raise ValueError("JSONの開始位置が見つかりませんでした")

    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(s[start:], start=start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return s[start: idx + 1]
    return s[start:]


def _remove_trailing_commas(text: str) -> str:
    prev = None
    cleaned = text
    while prev != cleaned:
        prev = cleaned
        cleaned = re.sub(r",\s*(?=[}\]])", "", cleaned)
    return cleaned


def _balance_json(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            if ch == stack[-1]:
                stack.pop()
            else:
                stack = []
                break
    if stack:
        return text + "".join(reversed(stack))
    return text


def _extract_json(text: str) -> dict[str, Any]:
    """モデル応答からJSONを抽出してパースする。"""
    raw = _strip_code_fence(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    try:
        return json.JSONDecoder().raw_decode(raw)[0]
    except (json.JSONDecodeError, ValueError):
        pass

    candidate = _extract_json_block(text)
    candidate = _remove_trailing_commas(candidate)
    candidate = _balance_json(candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "JSONブロックの末尾が見つかりませんでした。出力が不完全な可能性があります。"
            f" Raw snippet: {candidate[:400]!r}"
        ) from exc


def _build_user_prompt(
    video_title: str,
    video_url: str,
    transcript: str,
    posts_per_url: int = 1,
) -> str:
    payload = json.dumps(
        {
            "video_title": video_title,
            "video_url": video_url,
            "transcript": transcript,
        },
        ensure_ascii=False,
    )
    count = max(1, min(3, posts_per_url))
    if count == 1:
        count_rule = (
            "x_posts と threads_posts はそれぞれ **必ず 1 件のみ** 返してください "
            "(複数案を返さないこと)。"
        )
    else:
        count_rule = (
            f"x_posts と threads_posts はそれぞれ **必ず {count} 件** 返してください。"
            f" 各案は心理的トリガー / パターン / 切り口を変え、互いに重複しない別案にすること。"
            " (Skill定義の「1件のみ」指示はこの指示で上書きします)"
        )
    return (
        "以下のYouTube動画情報から、Skillに定義された戦略に厳密に従って "
        "X / Threads 向けの投稿案を生成してください。\n\n"
        "出力は指定のJSON Schemaのみ。前置き・後書き・コードフェンスは不要です。\n"
        f"{count_rule}\n\n"
        f"INPUT:\n{payload}"
    )


# ----- Gemini 経路 ----------------------------------------------------------


_gemini_client: Any | None = None


def _get_gemini_client() -> Any:
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        if not settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY が .env に未設定です (TEXT_PROVIDER=gemini)")
        _gemini_client = genai.Client(api_key=settings.google_api_key)
    return _gemini_client


def _generate_with_gemini(
    skill_md: str,
    user_prompt: str,
) -> GeneratedPosts:
    from google.genai import types

    client = _get_gemini_client()
    resp = client.models.generate_content(
        model=settings.gemini_text_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=skill_md,
            response_mime_type="application/json",
            response_schema=GeneratedPosts,
            max_output_tokens=16000,
            temperature=0.9,
        ),
    )

    # SDK が pydantic にパースして返してくれる場合はそれを使う。
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, GeneratedPosts):
        return parsed
    if isinstance(parsed, dict):
        return GeneratedPosts(**parsed)

    raw = getattr(resp, "text", None) or ""
    if not raw:
        raise RuntimeError("Geminiから空応答が返されました")
    data = _extract_json(raw)
    return GeneratedPosts(**data)


# ----- Claude 経路 ----------------------------------------------------------


_anthropic_client: Any | None = None


def _get_anthropic_client() -> Any:
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY が .env に未設定です (TEXT_PROVIDER=claude)")
        _anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


def _generate_with_claude(
    skill_md: str,
    user_prompt: str,
) -> GeneratedPosts:
    client = _get_anthropic_client()
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=16000,
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
                "content": [{"type": "text", "text": user_prompt}],
            }
        ],
    )

    if getattr(msg, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "Claude応答が max_tokens で打ち切られました。"
            "投稿件数を減らすか max_tokens を増やしてください。"
        )

    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    data = _extract_json(raw)
    return GeneratedPosts(**data)


# ----- 公開API -------------------------------------------------------------


def generate_posts(
    video_title: str,
    video_url: str,
    transcript: str,
    max_transcript_chars: int = 12000,
    posts_per_url: int = 1,
) -> GeneratedPosts:
    """字幕からX/Threadsの投稿文を生成する。TEXT_PROVIDER で実装が切り替わる。"""
    skill_md = _load_skill()
    user_prompt = _build_user_prompt(
        video_title=video_title,
        video_url=video_url,
        transcript=transcript[:max_transcript_chars],
        posts_per_url=posts_per_url,
    )

    provider = settings.text_provider
    if provider == "gemini":
        return _generate_with_gemini(skill_md, user_prompt)
    if provider == "claude":
        return _generate_with_claude(skill_md, user_prompt)
    raise RuntimeError(
        f"未知の TEXT_PROVIDER='{provider}' (gemini | claude のいずれかを指定)"
    )
