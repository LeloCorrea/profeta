"""Share card generator — cria social cards (1024×1792).

Pipeline principal: OpenAI Images API (qualidade premium).
Fallback: renderização local com Pillow.

Cache de dois níveis:
  1. Cache global por conteúdo: data/cards/global/{tipo}_{hash}.png
  2. Cache diário por usuário: data/cards/daily/{data}_{user_id}_{tipo}.seen

Rate limit: 1 geração por usuário por tipo por dia (total 4/dia).

Analytics: log_event registra geração, cache hit, rate limit e fallback.
"""

import base64
import datetime
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.observability import get_logger, log_event
from app.verse_service import format_verse_reference

# ── Pillow (fallback renderer) ─────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False

# ── OpenAI Images (primary renderer) ──────────────────────────────────────────
try:
    from openai import OpenAI as _OpenAI
    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    _OpenAI = None  # type: ignore[assignment,misc]
    _OPENAI_SDK_AVAILABLE = False

OPENAI_IMAGE_ENABLED: bool = _OPENAI_SDK_AVAILABLE and bool(os.getenv("OPENAI_API_KEY", "").strip())
_OPENAI_IMAGE_MODEL: str = os.getenv("OPENAI_IMAGE_MODEL", "dall-e-3")
_openai_img_client: Optional[Any] = None


def _get_openai_img_client() -> Any:
    global _openai_img_client
    if _openai_img_client is None:
        _openai_img_client = _OpenAI()
    return _openai_img_client


# ── Storage directories ────────────────────────────────────────────────────────
CARDS_DIR = Path("data/cards")
CARDS_DIR.mkdir(parents=True, exist_ok=True)


def _global_cards_dir() -> Path:
    d = CARDS_DIR / "global"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _daily_cards_dir() -> Path:
    d = CARDS_DIR / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


CARD_W, CARD_H = 1024, 1792

# ── Palette ────────────────────────────────────────────────────────────────────
_BG_TOP    = (10, 16, 40)          # #0A1028  deep navy
_BG_MID    = (16, 42, 92)          # #102A5C  royal navy
_BG_BOT    = (23, 59, 114)         # #173B72  sapphire
_WHITE       = (255, 255, 255, 255)
_WHITE_DIM   = (255, 255, 255, 195)
_WHITE_FADE  = (255, 255, 255, 140)
_SEP_COLOR   = (255, 255, 255, 60)
_WATERMARK   = (255, 255, 255, 14)   # ~5.5% — subtle, never intrusive
_GOLD        = (212, 175, 55, 235)   # #D4AF37 premium gold
_GOLD_LABEL  = (212, 175, 55, 200)
_GOLD_DIM    = (212, 175, 55, 160)

# ── Story safe-area layout constants ──────────────────────────────────────────
_SAFE_INSET_TOP = 252   # px from top clear of IG/WA Story chrome (time, indicators)
_SAFE_INSET_BOT = 160   # px from bottom clear of Story chrome (reply bar)
_FOOTER_H       = 350   # conservative height from separator line to last footer element
PAD_X           = 92    # horizontal layout padding (shared with draw helpers)

logger = get_logger(__name__)

CARD_LABELS: dict[str, str] = {
    "verse":       "VERSÍCULO DO DIA",
    "explanation": "EXPLICAÇÃO DO DIA",
    "reflection":  "REFLEXÃO DO DIA",
    "prayer":      "ORAÇÃO DO DIA",
}

# Text patterns that indicate generic fallback content — never share these.
_FALLBACK_PATTERNS = [
    "leia o texto novamente",
    "observe o que ele revela",
    "escolha uma atitude prática",
    "contemplar esta palavra com calma",
    "leia novamente com atenção",
    "senhor, que a verdade de",  # build_default_prayer generic template
]

# Card type metadata for prompt generation and visual differentiation.
_TYPE_META: dict[str, dict] = {
    "verse": {
        "label": "VERSÍCULO DO DIA",
        "emoji": "✨",
        "tone": "editorial, impactante e majestoso — cada palavra deve transmitir peso e beleza",
        "ref_display": "prominent",
    },
    "explanation": {
        "label": "EXPLICAÇÃO",
        "emoji": "📖",
        "tone": "educativo, aprofundado e didático — clareza e elegância intelectual",
        "ref_display": "secondary",
    },
    "reflection": {
        "label": "REFLEXÃO",
        "emoji": "🌿",
        "tone": "contemplativo, sereno e meditativo — silêncio e profundidade espiritual",
        "ref_display": "hidden",
    },
    "prayer": {
        "label": "ORAÇÃO",
        "emoji": "🙏",
        "tone": "íntimo, devocional e reverente — diálogo sussurrado com o sagrado",
        "ref_display": "hidden",
    },
}


# ── Public data ────────────────────────────────────────────────────────────────

@dataclass
class ShareCard:
    path: Path
    cache_hit: bool


# ── Content guards ─────────────────────────────────────────────────────────────

def is_shareable_content(text: str) -> bool:
    """Returns False for generic/fallback content that must not be shared."""
    if not text or len(text.strip()) < 40:
        return False
    lower = text.lower()
    return not any(p in lower for p in _FALLBACK_PATTERNS)


def smart_truncate(text: str, max_chars: int = 300) -> str:
    """Truncate at sentence boundary; never break in the middle of a phrase."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars + 80]
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = window.rfind(sep, 0, max_chars)
        if idx != -1 and idx > max_chars * 0.55:
            return window[:idx + 1].strip()
    idx = text[:max_chars].rfind(" ")
    if idx > int(max_chars * 0.6):
        return text[:idx].strip() + "..."
    return text[:max_chars].strip() + "..."


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _card_path(card_type: str, verse: dict[str, Any], content: str) -> Path:
    from app.audio_service import normalize_text
    book = normalize_text(str(verse.get("book", "livro"))) or "livro"
    chapter = str(verse.get("chapter", "0")).strip()
    verse_num = str(verse.get("verse", "0")).strip()
    h = _content_hash(content)
    return CARDS_DIR / f"{card_type}_{book}_{chapter}_{verse_num}_{h}.png"


def _normalize_content(content_type: str, text: str, reference: Optional[str]) -> str:
    """Stable canonical form of the content — used to produce the global cache key."""
    normalized_text = " ".join(text.split())
    ref_part = reference.strip() if reference else ""
    return f"{content_type}|{normalized_text}|{ref_part}"


def _compute_content_hash(normalized: str) -> str:
    """16-char SHA-256 prefix — uniquely identifies normalized content for global cache."""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _global_cache_path(content_hash: str, content_type: str) -> Path:
    return _global_cards_dir() / f"{content_type}_{content_hash}.png"


def _get_daily_cached(user_id: str, content_type: str) -> Optional[Path]:
    """Return today's already-generated image path for this user+type, or None."""
    date = datetime.date.today().isoformat()
    ptr = _daily_cards_dir() / f"{date}_{user_id}_{content_type}.path"
    if ptr.exists():
        try:
            p = Path(ptr.read_text().strip())
            if p.exists():
                return p
        except Exception:
            pass
    return None


def _enforce_daily_limit(user_id: str, content_type: str) -> bool:
    """Returns True if generation is allowed (not yet generated today for this type)."""
    date = datetime.date.today().isoformat()
    seen = _daily_cards_dir() / f"{date}_{user_id}_{content_type}.seen"
    return not seen.exists()


def _mark_daily_generated(user_id: str, content_type: str, image_path: Path) -> None:
    date = datetime.date.today().isoformat()
    daily = _daily_cards_dir()
    (daily / f"{date}_{user_id}_{content_type}.seen").write_text("1")
    (daily / f"{date}_{user_id}_{content_type}.path").write_text(str(image_path))


# ── Font loader ────────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False, serif: bool = False):  # -> ImageFont
    if not _PILLOW_AVAILABLE:
        return None
    if serif:
        for path in [
            "C:/Windows/Fonts/georgia.ttf",
            "C:/Windows/Fonts/times.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
            "/System/Library/Fonts/Times.ttc",
            "/Library/Fonts/Georgia.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        logger.warning("share_service: no serif font available; card rendered in sans-serif")
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    elif bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ── Drawing helpers ────────────────────────────────────────────────────────────

def _tw(font, text: str) -> int:
    """Text pixel width."""
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * (font.size if hasattr(font, "size") else 10)


def _th(font, text: str = "Ag") -> int:
    """Text pixel height."""
    try:
        bbox = font.getbbox(text)
        return bbox[3] - bbox[1]
    except Exception:
        return font.size if hasattr(font, "size") else 16


def _cx(font, text: str) -> int:
    """X position to center text horizontally."""
    return (CARD_W - _tw(font, text)) // 2


def _draw_centered(draw, text: str, font, color: tuple, y: int) -> None:
    draw.text((_cx(font, text), y), text, font=font, fill=color)


def _wrap(text: str, font, max_w: int) -> list[str]:
    """Word-wrap text to fit max_w pixels."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        test = f"{cur} {word}".strip() if cur else word
        if _tw(font, test) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


# ── Visual effect helpers ──────────────────────────────────────────────────────

def _apply_vignette(img: "Image.Image") -> None:
    """Darken edges for depth — pure Pillow, no numpy."""
    center = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(center).ellipse(
        [160, 300, CARD_W - 160, CARD_H - 300], fill=255
    )
    center = center.filter(ImageFilter.GaussianBlur(160))
    edge_alpha = ImageOps.invert(center).point(lambda x: int(x * 62 / 255))
    overlay = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 255))
    overlay.putalpha(edge_alpha)
    img.alpha_composite(overlay)


def _apply_light(img: "Image.Image") -> None:
    """Soft top-right warm glow — pure Pillow, no numpy."""
    glow = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    cx, cy = int(CARD_W * 0.80), int(CARD_H * 0.16)
    ImageDraw.Draw(glow).ellipse(
        [cx - 520, cy - 420, cx + 520, cy + 420],
        fill=(140, 170, 230, 48),
    )
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(85)))


def _draw_diamond_separator(draw: "ImageDraw.ImageDraw", y: int) -> None:
    """Separator line with a centered diamond accent."""
    x1, x2, cx = PAD_X + 40, CARD_W - PAD_X - 40, CARD_W // 2
    d = 6
    draw.rectangle([x1, y, cx - d - 14, y + 1], fill=_SEP_COLOR)
    draw.polygon(
        [(cx, y - d), (cx + d, y), (cx, y + d), (cx - d, y)],
        fill=(*_GOLD[:3], 155),
    )
    draw.rectangle([cx + d + 14, y, x2, y + 1], fill=_SEP_COLOR)


def _draw_footer_card(draw: "ImageDraw.ImageDraw", top: int, bottom: int) -> None:
    """Subtle rounded panel behind footer for depth."""
    x1, x2, r = PAD_X - 30, CARD_W - PAD_X + 30, 16
    fill = (255, 255, 255, 10)
    draw.rectangle([x1 + r, top, x2 - r, bottom], fill=fill)
    draw.rectangle([x1, top + r, x2, bottom - r], fill=fill)
    for ex, ey in [(x1, top), (x2 - 2*r, top), (x1, bottom - 2*r), (x2 - 2*r, bottom - 2*r)]:
        draw.ellipse([ex, ey, ex + 2*r, ey + 2*r], fill=fill)


# ── Card renderer (Pillow fallback) ───────────────────────────────────────────

def _render_card(
    card_type: str,
    verse: dict[str, Any],
    main_text: str,
    app_name: str,
    instagram_handle: str,
    telegram_handle: str,
    telegram_link: str,
    cta_line: str,
) -> "Image.Image":
    img = Image.new("RGBA", (CARD_W, CARD_H))
    draw = ImageDraw.Draw(img)

    # ── Gradient background ────────────────────────────────────────────────────
    for row in range(CARD_H):
        t = row / CARD_H
        if t < 0.5:
            c = tuple(int(_BG_TOP[i] + (_BG_MID[i] - _BG_TOP[i]) * (t * 2)) for i in range(3))
        else:
            c = tuple(int(_BG_MID[i] + (_BG_BOT[i] - _BG_MID[i]) * ((t - 0.5) * 2)) for i in range(3))
        draw.line([(0, row), (CARD_W, row)], fill=(*c, 255))

    _apply_vignette(img)
    _apply_light(img)
    draw = ImageDraw.Draw(img)

    # ── Watermark — subtle, bottom-anchored, never competes with content ───────
    wm_font = _load_font(65, bold=True)
    wm_x = _cx(wm_font, "PROFETA")
    wm_y = int(CARD_H * 0.87)
    draw.text((wm_x, wm_y), "PROFETA", font=wm_font, fill=_WATERMARK)

    # ── Layout ────────────────────────────────────────────────────────────────
    content_w = CARD_W - PAD_X * 2
    y = _SAFE_INSET_TOP  # Story-safe top margin — clear of status bar and indicators
    sep_y_max = CARD_H - _SAFE_INSET_BOT - _FOOTER_H  # separator ceiling for safe area

    # Section label — wide letter-spacing for premium editorial feel
    lbl_font = _load_font(28)
    label = CARD_LABELS.get(card_type, card_type.upper())
    spaced = "   ".join(label)
    _draw_centered(draw, spaced, lbl_font, _GOLD_LABEL, y)
    y += _th(lbl_font, spaced) + 30

    # Gold accent rule
    deco_len = 80
    dx = (CARD_W - deco_len) // 2
    draw.rectangle([dx, y, dx + deco_len, y + 2], fill=(*_GOLD[:3], 160))
    y += 58

    # ── Main text block ────────────────────────────────────────────────────────
    # Probe with 52px to choose final size: ≤2 lines → 56px, ≤5 → 52px, else 48px.
    _probe = _load_font(52, serif=True)
    _rough_n = len(_wrap(main_text, _probe, content_w))
    text_size = 56 if _rough_n <= 2 else 52 if _rough_n <= 5 else 48
    text_font = _load_font(text_size, serif=True)
    lh = int(_th(text_font) * 1.62)
    # Clip lines so text never pushes reference + footer off-canvas.
    # 220 reserves: post-text gap (64) + ref+padding (~130) + sep gap (20) + margin.
    _max_lines = max(1, (sep_y_max - y - 220) // lh)
    lines = _wrap(main_text, text_font, content_w)[:_max_lines]
    block_h = len(lines) * lh

    zone_top = y
    zone_bot = int(CARD_H * 0.64)
    zone_center = (zone_top + zone_bot) // 2
    y_text = max(zone_center - block_h // 2, zone_top + 40)

    q_size = max(52, min(76, int(text_size * 1.45)))
    q_font = _load_font(q_size, serif=True)
    q_color = (*_GOLD[:3], 155)
    x_first = (CARD_W - _tw(text_font, lines[0])) // 2
    draw.text(
        (max(PAD_X, x_first - _tw(q_font, "“") - 6), y_text - _th(q_font) // 3),
        "“",
        font=q_font,
        fill=q_color,
    )

    for line in lines:
        x = (CARD_W - _tw(text_font, line)) // 2
        draw.text((x, y_text), line, font=text_font, fill=_WHITE)
        y_text += lh

    x_last_end = (CARD_W + _tw(text_font, lines[-1])) // 2
    draw.text(
        (min(CARD_W - PAD_X - _tw(q_font, "”"), x_last_end + 6),
         y_text - lh + _th(text_font) - _th(q_font) // 4),
        "”",
        font=q_font,
        fill=q_color,
    )
    y = y_text + 64

    # ── Biblical reference — verse: prominent; explanation: secondary; reflection/prayer: hidden ──
    ref = format_verse_reference(verse)
    if ref and card_type in ("verse", "explanation"):
        ref_size = 44 if card_type == "verse" else 34
        ref_color = _GOLD if card_type == "verse" else _GOLD_DIM
        ref_font = _load_font(ref_size)
        _draw_centered(draw, f"— {ref}", ref_font, ref_color, y)
        y += _th(ref_font) + (80 if card_type == "verse" else 56)

    # ── Separator ─────────────────────────────────────────────────────────────
    # Floor at 68% keeps footer proportion; ceiling enforces Story safe area.
    sep_y = min(max(y + 20, int(CARD_H * 0.68)), sep_y_max)
    _draw_diamond_separator(draw, sep_y)

    # ── Footer ────────────────────────────────────────────────────────────────
    _draw_footer_card(draw, sep_y + 36, CARD_H - _SAFE_INSET_BOT + 8)
    fy = sep_y + 72

    name_font = _load_font(44, bold=True)
    brand_name = f"{app_name} — Oficial"
    _draw_centered(draw, brand_name, name_font, _WHITE, fy)
    fy += int(_th(name_font) * 1.70)

    cta_font = _load_font(32)
    cta_display = cta_line if "✨" in cta_line else f"{cta_line} ✨"
    _draw_centered(draw, cta_display, cta_font, _WHITE_DIM, fy)
    fy += int(_th(cta_font) * 1.85)

    hdl_font = _load_font(29, bold=True)
    handles = f"{telegram_handle}  ·  {instagram_handle}"
    _draw_centered(draw, handles, hdl_font, _GOLD_DIM, fy)

    return img.convert("RGB")


# ── OpenAI prompt builder ──────────────────────────────────────────────────────

def _build_image_prompt(
    content_type: str,
    text: str,
    reference: Optional[str],
    app_name: str = "O Profeta",
    instagram_handle: str = "@oprofeta.oficial",
    telegram_handle: str = "@profeta_oficial_bot",
    cta_line: str = "Receba sua Palavra diária no Telegram",
) -> str:
    meta = _TYPE_META.get(content_type, _TYPE_META["verse"])
    label = meta["label"]
    emoji = meta["emoji"]
    tone = meta["tone"]
    ref_display = meta["ref_display"]
    truncated = smart_truncate(text, max_chars=280)

    # ── Reference block ────────────────────────────────────────────────────────
    ref_block = ""
    if reference and ref_display != "hidden":
        if ref_display == "prominent":
            ref_block = (
                f"TYPOGRAPHY — BIBLICAL REFERENCE (directly below main text, centered): "
                f"Text: '— {reference}'. "
                f"Style: elegant serif, warm gold #D4AF37, clearly readable. "
                f"Spacing: generous margin above and below. "
            )
        else:
            ref_block = (
                f"TYPOGRAPHY — BIBLICAL REFERENCE (below main text, centered): "
                f"Text: '— {reference}'. "
                f"Style: elegant serif, muted gold #D4AF3790, smaller than main text. "
            )

    # ── Footer ────────────────────────────────────────────────────────────────
    handles = f"{telegram_handle} • {instagram_handle}"

    prompt = (
        f"Premium Christian editorial card. Portrait 1024×1792px. "
        f"SaaS-grade luxury design — EXTREMELY premium, top-tier studio execution. {emoji}\n\n"

        f"BACKGROUND: Silky vertical gradient — deep midnight navy #0A1028 at top, "
        f"rich royal navy #102A5C at center, sapphire #173B72 at bottom. "
        f"Subtle edge vignette for depth. Soft warm-cool radial glow in upper-right quadrant.\n\n"

        f"SECTION LABEL (top, centered): '{label}'. "
        f"Uppercase sans-serif, extreme letter-spacing, warm gold #D4AF37, small size. "
        f"Short thin centered gold rule below.\n\n"

        f"MAIN TEXT (vertical center, centered): \"{truncated}\" "
        f"Premium serif, white #FFFFFF, large and commanding — dominant element. "
        f"Generous line-height, perfect letter-spacing. "
        f"Decorative golden curly opening quote above-left, closing quote below-right.\n\n"

        f"{ref_block}"

        f"SEPARATOR: Thin translucent white line, gold ◆ diamond at exact center.\n\n"

        f"WATERMARK: 'PROFETA', centered lower-middle, VERY LARGE (spans most of card width), "
        f"10 to 15 percent opacity, white bold sans-serif. Ghosted brand presence, non-intrusive.\n\n"

        f"FOOTER (below separator, centered, clean sans-serif): "
        f"'{app_name}' bold white. '{cta_line}'. "
        f"'{telegram_handle}' gold. '{handles}' smaller gold. Premium line spacing.\n\n"

        f"QUALITY (non-negotiable): EXTREMELY premium SaaS product look. "
        f"Clean and minimalist — nothing decorative for its own sake. "
        f"High perceived value at every detail. Perfect typographic hierarchy throughout. "
        f"Zero visual noise. No photographs — pure typography and geometry only.\n\n"

        f"TONE: {tone}. Elegant Christian aesthetic.\n\n"

        f"HIERARCHY: main text dominant, label secondary, "
        f"the footer must be visually separated and tertiary — top→center→bottom flow.\n\n"

        f"STRICTLY AVOID: no clutter, no cheap gradients, no amateur design, no cartoon style.\n\n"

        f"TYPOGRAPHY: Elegant serif for main text, refined sans-serif for labels/footer, "
        f"excellent spacing, no crowding.\n\n"

        f"SPACING: Generous margins, clear separation between sections, strong breathing room."
    )
    log_event(logger, "share_prompt_built", length=len(prompt), content_type=content_type)
    return prompt


# ── OpenAI image generation ────────────────────────────────────────────────────

def _generate_image_via_openai(prompt: str) -> Optional[bytes]:
    """Call OpenAI Images API. Returns PNG bytes or None on any failure."""
    if not OPENAI_IMAGE_ENABLED:
        return None
    try:
        client = _get_openai_img_client()
        response = client.images.generate(
            model=_OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1792",
            quality="hd",
            response_format="b64_json",
            n=1,
        )
        b64_data = response.data[0].b64_json
        return base64.b64decode(b64_data)
    except Exception as exc:
        log_event(
            logger,
            "share_card_openai_error",
            error=str(exc)[:200],
            level=logging.WARNING,
        )
        return None


# ── Image orchestration ────────────────────────────────────────────────────────

def _get_or_create_image(
    card_type: str,
    verse: dict[str, Any],
    main_text: str,
    user_id: Optional[str],
    app_name: str,
    instagram_handle: str,
    telegram_handle: str,
    telegram_link: str,
    cta_line: str,
) -> tuple[Optional[Path], bool]:
    """
    Returns (path, cache_hit). Decision order:
      1. Daily user cache → return existing image
      2. Global content cache → return existing image, register for user
      3. Rate limit check → block if user exhausted today's quota
      4. Generate via OpenAI (primary)
      5. Render via Pillow (fallback)
    """
    ref = format_verse_reference(verse) or None

    # 1. Daily user cache
    if user_id:
        cached = _get_daily_cached(user_id, card_type)
        if cached:
            log_event(logger, "share_card_cache_hit_daily", card_type=card_type)
            return cached, True

    # 2. Global content cache
    normalized = _normalize_content(card_type, main_text, ref)
    content_hash = _compute_content_hash(normalized)
    global_path = _global_cache_path(content_hash, card_type)
    if global_path.exists():
        log_event(logger, "share_card_cache_hit_global", card_type=card_type)
        if user_id:
            _mark_daily_generated(user_id, card_type, global_path)
        return global_path, True

    # 3. Rate limit (both caches missed)
    if user_id and not _enforce_daily_limit(user_id, card_type):
        log_event(logger, "share_card_rate_limited", card_type=card_type)
        return None, False

    # 4. Try OpenAI
    img_bytes: Optional[bytes] = None
    if OPENAI_IMAGE_ENABLED:
        prompt = _build_image_prompt(
            card_type, main_text, ref,
            app_name=app_name,
            instagram_handle=instagram_handle,
            telegram_handle=telegram_handle,
            cta_line=cta_line,
        )
        img_bytes = _generate_image_via_openai(prompt)
        if img_bytes:
            log_event(logger, "share_card_generated_openai", card_type=card_type)

    # 5. Pillow fallback
    if img_bytes is None:
        if not _PILLOW_AVAILABLE:
            log_event(logger, "share_card_unavailable", reason="both_backends_unavailable")
            return None, False
        img = _render_card(
            card_type=card_type,
            verse=verse,
            main_text=main_text,
            app_name=app_name,
            instagram_handle=instagram_handle,
            telegram_handle=telegram_handle,
            telegram_link=telegram_link,
            cta_line=cta_line,
        )
        global_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(global_path), "PNG", compress_level=6)
        log_event(logger, "share_card_generated_pillow", card_type=card_type)
    else:
        global_path.parent.mkdir(parents=True, exist_ok=True)
        global_path.write_bytes(img_bytes)

    if user_id:
        _mark_daily_generated(user_id, card_type, global_path)

    log_event(logger, "share_card_generated", card_type=card_type, cache_hit=False)
    return global_path, False


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_share_card(
    card_type: str,
    verse: dict[str, Any],
    main_text: str,
    *,
    app_name: str = "O Profeta",
    instagram_handle: str = "@oprofeta.oficial",
    telegram_handle: str = "@profeta_oficial_bot",
    telegram_link: str = "t.me/profeta_oficial_bot",
    cta_line: str = "Receba sua Palavra diária no Telegram",
    referral_code: Optional[str] = None,  # reserved: QR code / deep link viral_USERID
    user_id: Optional[str] = None,
) -> Optional[ShareCard]:
    """Generate (or return cached) a 1024×1792 social card for sharing.

    Primary path: OpenAI Images API.
    Fallback: Pillow local rendering.

    Returns None if:
    - Content is detected as generic fallback (never share low-quality content)
    - Neither OpenAI nor Pillow is available
    - User's daily rate limit is exhausted for this content type
    """
    main_text = main_text.strip()
    if not is_shareable_content(main_text):
        log_event(
            logger,
            "share_card_blocked",
            card_type=card_type,
            reason="fallback_content",
            verse_reference=format_verse_reference(verse),
        )
        return None

    truncated = smart_truncate(main_text)
    path, cache_hit = _get_or_create_image(
        card_type=card_type,
        verse=verse,
        main_text=truncated,
        user_id=user_id,
        app_name=app_name,
        instagram_handle=instagram_handle,
        telegram_handle=telegram_handle,
        telegram_link=telegram_link,
        cta_line=cta_line,
    )
    if path is None:
        return None
    return ShareCard(path=path, cache_hit=cache_hit)
