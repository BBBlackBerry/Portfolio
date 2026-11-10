"""投稿用画像の生成 (Google Gemini で図 → PIL で日本語テキストを後合成).

Gemini の画像生成モデル (Nano Banana / 2.5 Flash Image) は日本語、特に漢字の
レンダリングが構造的に弱く、`脱属人` が `脱属人` になるなど崩れが頻発する。
そこで本モジュールは以下のハイブリッド方式で日本語の文字化けを構造的に排除する:

  1. Gemini には「日本語テキストを画像内に一切描かない・上下バンドは空白で確保する」
     と強く指示し、中央バンドの **図 (ダイアグラム) だけ** を描いてもらう。
  2. 戻ってきた画像の上下バンドをキャンバス色で塗り潰し、その上に Pillow と
     ヒラギノ角ゴシック (W3/W6) でバッジ・見出し・フッターを焼き込む。

これにより、生成モデルの揺らぎに関係なく、毎回 pixel-perfect な日本語を担保する。
"""

from __future__ import annotations

import io
import re
import time
import uuid
from pathlib import Path

from google import genai
from PIL import Image, ImageDraw, ImageFont

from .config import settings

CHANNEL_HANDLE_FOOTER = "脱・属人チャンネル｜AI×DX"


def _ensure_channel_footer(footer: str) -> str:
    """画像下部のフッターは常にチャンネル署名のみに固定する."""
    del footer
    return CHANNEL_HANDLE_FOOTER


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY が .env に未設定です")
        _client = genai.Client(api_key=settings.google_api_key)
    return _client


_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s")


def _parse_retry_delay_seconds(err_text: str) -> float | None:
    m = _RETRY_DELAY_RE.search(err_text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Gemini プロンプト
# ---------------------------------------------------------------------------
# 重要: 画像内に日本語テキストは一切描かせない。Gemini は図だけを担当する。
_GEMINI_STYLE_DIRECTIVE = (
    "ROLE: You are an award-winning editorial art director designing a square (1:1) "
    "DIAGRAM-FIRST infographic for a social post about a specific YouTube video. "
    "The image's primary job is to EXPLAIN A SYSTEM VISUALLY through a clean 2D diagram. "
    "All Japanese text (badge / headline / footer / node labels) will be composited later by "
    "post-processing — so you must NOT render any Japanese characters yourself.\n\n"
    "MISSION:\n"
    "- Render a clean 2D SYSTEM DIAGRAM (flat top-down pipeline / node graph / flow chart with arrows) "
    "that visualizes the actual workflow described in SUBJECT: inputs → processing stages → outputs, "
    "with the specific tools, data, and steps named in SUBJECT shown as labeled blocks / icons / cards.\n"
    "- Use ICONS, SHAPES, ARROWS, SMALL CHARTS, and CONNECTOR LINES to do the storytelling. "
    "The viewer should understand the SYSTEM at a glance from the visual alone.\n\n"
    "RESERVED EMPTY ZONES (CRITICAL — these will be overlaid with Japanese text in post-processing):\n"
    "- TOP BAND: the top ~22% of the canvas must be COMPLETELY EMPTY negative space — canvas color only, "
    "no text, no icons, no shapes, no gradient marks. Treat it as a sacred margin.\n"
    "- BOTTOM BAND: the bottom ~12% of the canvas must be COMPLETELY EMPTY negative space, same rule.\n"
    "- The system diagram lives ONLY in the middle ~66% of the canvas.\n\n"
    "VISUAL STYLE (fixed):\n"
    "- Premium editorial infographic, STRICTLY 2D flat vector design — NO isometric projection, NO 3D depth, "
    "NO perspective. Top-down or straight-on view only.\n"
    "- Monoline 2px stroke icons, subtle drop shadows only for layering hint, clean asymmetric layout, "
    "generous negative space, strong visual hierarchy.\n"
    "- The system diagram occupies the middle band, centered both horizontally and vertically inside it. "
    "Connector arrows are clearly directional. Each node is an icon-only card / chip / shape.\n"
    "- NODE LABELS — ZERO TEXT INSIDE THE DIAGRAM. No Japanese characters, NO Latin/ASCII letters, NO "
    "brand names, NO tool names, NO abbreviations, NO chart/axis labels, NO callouts. Image generation "
    "models routinely misspell brand names (e.g. 'ChatGPT' becomes 'ChaTCPT', 'Notion' becomes 'Noiton'), "
    "so we eliminate the risk by banning text inside the diagram entirely. Each node is represented "
    "PURELY by a distinct graphical icon or shape — make icons visually distinct enough that the viewer "
    "can tell nodes apart without any text.\n"
    "- ONLY EXCEPTION to the text ban: single-digit step numerals (1, 2, 3, 4) inside small circles to "
    "mark sequence order. Nothing else.\n"
    "- Add 2–4 supporting visual elements (small bar/donut chart shapes WITHOUT text labels, progress "
    "indicator, data-packet glyphs along arrows, step numerals 1/2/3 in circles) that reinforce the "
    "SUBJECT story.\n"
    "- Cinematic feel: bold figure-ground contrast, decisive accent color, soft gradient background.\n\n"
    "PALETTE (fixed):\n"
    "- Canvas: off-white #F8FAFC with a barely-perceptible cool gradient.\n"
    "- Ink / primary outlines: deep navy #0F172A.\n"
    "- Primary accent (hero outlines, key arrows): electric blue #2563EB.\n"
    "- Secondary accents used sparingly for icons / dividers / data marks: emerald #16A34A and amber #F59E0B.\n"
    "- No neon, no rainbow, no muddy tones.\n\n"
    "ABSOLUTE PROHIBITIONS:\n"
    "- DO NOT render ANY text on the image — no Japanese, no Latin/ASCII letters, no brand names, no "
    "tool names, no acronyms, no abbreviations, no chart labels, no captions, no watermarks. The ONLY "
    "permitted glyphs are single-digit step numerals (1, 2, 3, 4) inside small circles. This is the "
    "single most important rule — violating it produces misspelled brand names which is unacceptable.\n"
    "- DO NOT render hashtags, URLs, @handles, dates, or watermarks.\n"
    "- No brand logos (Google, OpenAI, official YouTube red logo, etc.). Use generic monochrome icons only.\n"
    "- No profile pictures, avatars, faces, or human figures.\n"
    "- No photorealism, no 3D rendering, no isometric projection, no watercolor, no hand-drawn sketch — "
    "strictly 2D flat editorial vector only.\n"
    "- No lorem ipsum, no dummy text, no decorative scribbles meant to look like text.\n"
    "- No generic automation clichés (robots, light bulbs, gears floating in clouds, generic office scenes) "
    "unless explicitly named in SUBJECT."
)


def _build_gemini_prompt(subject: str, *, diagram_hints: list[str]) -> str:
    hints = [h.strip() for h in diagram_hints if h and h.strip()]
    hints_block = (
        "\n".join(f"  - {h}" for h in hints)
        if hints
        else "  (no extra hints — derive the diagram entirely from SUBJECT)"
    )
    parts = [
        _GEMINI_STYLE_DIRECTIVE,
        "",
        "SUBJECT (the reference video — the system diagram must visualize THIS, not generic automation):",
        subject.strip() or "(no subject provided — fall back to a minimal abstract editorial composition)",
        "",
        "DIAGRAM HINTS (turn each idea below into a 2D node / icon / connector in the system diagram. "
        "Represent each as a visual element only — DO NOT render the sentence itself as text on the image. "
        "Japanese text will be composited later by post-processing.):",
        hints_block,
        "",
        "REMINDER: render ZERO text anywhere on the image — no Japanese, no Latin letters, no brand "
        "names, no tool names (only single-digit step numerals 1/2/3/4 in circles are allowed). Every "
        "node is icon-only. The top ~22% and bottom ~12% of the canvas must be left as completely empty "
        "negative space (canvas color only).",
    ]
    return "\n".join(parts)


def _gemini_generate(
    subject: str,
    *,
    diagram_hints: list[str],
    retried: bool = False,
) -> bytes:
    client = _get_client()
    full_prompt = _build_gemini_prompt(subject, diagram_hints=diagram_hints)
    try:
        resp = client.models.generate_content(
            model=settings.image_model,
            contents=[full_prompt],
        )
    except Exception as e:
        msg = str(e)
        if not retried and ("429" in msg or "RESOURCE_EXHAUSTED" in msg):
            delay = _parse_retry_delay_seconds(msg)
            if delay is not None and delay <= 30:
                time.sleep(delay + 0.5)
                return _gemini_generate(subject, diagram_hints=diagram_hints, retried=True)
        raise
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data
    raise RuntimeError("Gemini画像生成APIから画像データを取得できませんでした")


# ---------------------------------------------------------------------------
# PIL 後合成: バッジ / 見出し / フッターを焼き込む
# ---------------------------------------------------------------------------

_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W5.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
]
_FONT_CANDIDATES_REGULAR = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
]


def _resolve_font_path(*, bold: bool) -> str:
    candidates = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    for p in candidates:
        if Path(p).exists():
            return p
    raise RuntimeError("日本語フォントが見つかりません (Hiragino / Noto Sans CJK JP を導入してください)")


def _font(size_px: float, *, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_resolve_font_path(bold=bold), int(round(size_px)))


CANVAS_BG = (248, 250, 252)       # #F8FAFC
INK_NAVY = (15, 23, 42)            # #0F172A
ACCENT_BLUE = (37, 99, 235)        # #2563EB
SLATE_GRAY = (71, 85, 105)         # #475569
WHITE = (255, 255, 255)

# レイアウト比率 (canvas の短辺基準)。1024x1024 を想定して設計し、他サイズにも比例適用。
LAYOUT = {
    "margin": 0.058,           # 外側余白 ≈ 60/1024
    "top_band": 0.22,          # 上バンド 22%
    "bottom_band": 0.12,       # 下バンド 12%
    "badge_height": 0.058,     # バッジ高さ
    "badge_font": 0.027,
    "badge_padx": 0.024,
    "headline_font": 0.062,    # 見出しフォントサイズ
    "headline_top_gap": 0.024, # バッジと見出しの間
    "headline_line_gap": 1.18, # 行送り倍率
    "footer_font": 0.024,
    "footer_glyph": 0.038,
    "footer_bottom_margin": 0.045,
}


_WRAP_TOKEN_RE = re.compile(r"\n|[A-Za-z0-9]+|.")


def _tokenize_for_wrap(text: str) -> list[str]:
    """折り返し用にテキストをトークン化する。

    - "\n" は改行トークン
    - ASCII 英数字の連続は 1 トークン (語の途中で改行されないようにする)
    - それ以外は 1 文字 1 トークン (日本語は文字境界で折り返し可)
    """
    return _WRAP_TOKEN_RE.findall(text)


def _wrap_japanese(text: str, font: ImageFont.FreeTypeFont, max_width: float, max_lines: int) -> list[str]:
    """トークン単位の貪欲折り返し。

    日本語は1文字単位で折り返し可だが、ASCII 英数字の連続 (例: "Genspark", "ChatGPT") は
    1 つの語として扱い、語の途中で改行されないようにする。語単独で max_width を超える
    病的ケースは最終手段として文字単位で切る。
    """
    if not text:
        return []
    tokens = _tokenize_for_wrap(text)
    lines: list[str] = []
    current = ""

    def commit() -> None:
        nonlocal current
        lines.append(current.rstrip(" "))
        current = ""

    idx = 0
    while idx < len(tokens) and len(lines) < max_lines:
        tok = tokens[idx]
        if tok == "\n":
            commit()
            idx += 1
            continue
        # 行頭の空白は捨てる
        if not current and tok == " ":
            idx += 1
            continue
        trial = current + tok
        if font.getlength(trial) <= max_width:
            current = trial
            idx += 1
            continue
        if current:
            # 今のトークンは入らない: 行をコミットして同じトークンを次行で再試行
            commit()
            continue
        # current が空でこのトークン単独でも幅超過 (語が幅より長い病的ケース)
        if len(tok) > 1:
            cut = len(tok)
            while cut > 1 and font.getlength(tok[:cut]) > max_width:
                cut -= 1
            current = tok[:cut]
            tokens[idx] = tok[cut:]
            commit()
            continue
        # 1文字でも幅超過: そのまま載せる
        current = tok
        idx += 1

    if current and len(lines) < max_lines:
        commit()

    # 全トークンを消費できていなければ末尾を「…」で省略
    if idx < len(tokens) and lines:
        last = lines[-1]
        while last and font.getlength(last + "…") > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    *,
    height: float,
    pad_x: float,
    font: ImageFont.FreeTypeFont,
    fill_bg=ACCENT_BLUE,
    fill_text=WHITE,
) -> tuple[float, float]:
    """バッジ pill を描画し、占有矩形の右下座標を返す."""
    text_w = font.getlength(text)
    pill_w = text_w + pad_x * 2
    radius = height / 2
    draw.rounded_rectangle([x, y, x + pill_w, y + height], radius=radius, fill=fill_bg)
    # 文字の縦中心。getbbox の上端オフセットで上下バランスを取る。
    bbox = font.getbbox(text)
    text_h = bbox[3] - bbox[1]
    text_y = y + (height - text_h) / 2 - bbox[1]
    draw.text((x + pad_x, text_y), text, font=font, fill=fill_text)
    return x + pill_w, y + height


def _draw_youtube_glyph(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    size: float,
    fill=SLATE_GRAY,
) -> None:
    radius = size * 0.25
    draw.rounded_rectangle([x, y, x + size, y + size], radius=radius, fill=fill)
    cx = x + size / 2
    cy = y + size / 2
    tri_h = size * 0.42
    tri_w = size * 0.36
    pts = [
        (cx - tri_w * 0.40, cy - tri_h / 2),
        (cx - tri_w * 0.40, cy + tri_h / 2),
        (cx + tri_w * 0.70, cy),
    ]
    draw.polygon(pts, fill=WHITE)


def _overlay_text(image_bytes: bytes, *, badge: str, headline: str, footer: str) -> bytes:
    """Gemini が生成した画像の上下バンドを塗り潰し、日本語テキストを焼き込む."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    W, H = img.size
    short = min(W, H)

    margin = LAYOUT["margin"] * short
    top_band = LAYOUT["top_band"] * H
    bottom_band = LAYOUT["bottom_band"] * H

    # 上下バンドをキャンバス色で塗り潰す (Gemini の漏れを無効化)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, top_band], fill=CANVAS_BG)
    draw.rectangle([0, H - bottom_band, W, H], fill=CANVAS_BG)

    # --- バッジ ---
    badge_font = _font(LAYOUT["badge_font"] * short, bold=True)
    badge_height = LAYOUT["badge_height"] * short
    badge_padx = LAYOUT["badge_padx"] * short
    badge_y = margin
    if badge.strip():
        _draw_badge(
            draw, margin, badge_y, badge.strip(),
            height=badge_height, pad_x=badge_padx, font=badge_font,
        )

    # --- 見出し ---
    if headline.strip():
        headline_font = _font(LAYOUT["headline_font"] * short, bold=True)
        headline_top = badge_y + badge_height + LAYOUT["headline_top_gap"] * short
        max_text_w = W - margin * 2
        lines = _wrap_japanese(headline.strip(), headline_font, max_text_w, max_lines=2)
        # 行の高さ = font.size * line_gap
        line_h = int(headline_font.size * LAYOUT["headline_line_gap"])
        for i, line in enumerate(lines):
            draw.text(
                (margin, headline_top + i * line_h),
                line,
                font=headline_font,
                fill=INK_NAVY,
            )

    # --- フッター (YouTube glyph + テキスト) ---
    footer_text = footer.strip() or CHANNEL_HANDLE_FOOTER
    footer_font = _font(LAYOUT["footer_font"] * short, bold=False)
    glyph_size = LAYOUT["footer_glyph"] * short
    glyph_x = margin
    glyph_y = H - bottom_band + (bottom_band - glyph_size) / 2
    _draw_youtube_glyph(draw, glyph_x, glyph_y, glyph_size)
    text_x = glyph_x + glyph_size + glyph_size * 0.35
    # 縦中心揃え
    bbox = footer_font.getbbox(footer_text)
    text_h = bbox[3] - bbox[1]
    text_y = glyph_y + (glyph_size - text_h) / 2 - bbox[1]
    draw.text((text_x, text_y), footer_text, font=footer_font, fill=SLATE_GRAY)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    *,
    badge_jp: str = "",
    headline_jp: str = "",
    subhead_jp: str = "",
    bullets_jp: list[str] | None = None,
    footer_jp: str = "",
    size: str = "1024x1024",
) -> Path:
    """Gemini で図を生成 → PIL で日本語テキストを後合成し、保存先パスを返す."""
    del size  # 互換のため受けるが内部では未使用
    footer_jp = _ensure_channel_footer(footer_jp)

    diagram_hints: list[str] = []
    if subhead_jp.strip():
        diagram_hints.append(subhead_jp.strip())
    diagram_hints.extend([b for b in (bullets_jp or []) if b and b.strip()])

    raw = _gemini_generate(prompt, diagram_hints=diagram_hints)
    composed = _overlay_text(raw, badge=badge_jp, headline=headline_jp, footer=footer_jp)

    fname = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    path = settings.output_dir / fname
    path.write_bytes(composed)
    return path
