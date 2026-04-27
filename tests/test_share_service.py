"""Tests for share_service — card generation, content guards, cache, and rate limit."""

import hashlib
from pathlib import Path

import pytest

from app.share_service import (
    ShareCard,
    generate_share_card,
    is_shareable_content,
    smart_truncate,
    _content_hash,
    _card_path,
    _normalize_content,
    _compute_content_hash,
    _enforce_daily_limit,
    _build_image_prompt,
    CARDS_DIR,
    OPENAI_IMAGE_ENABLED,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

VERSE = {
    "book": "João",
    "chapter": "3",
    "verse": "16",
    "text": "Porque Deus amou o mundo de tal maneira que deu o seu Filho unigênito.",
}

LONG_EXPLANATION = (
    "Este versículo revela a profundidade do amor divino pela humanidade. "
    "No contexto histórico, foi escrito num momento de grande transformação espiritual. "
    "A palavra grega agápē, usada aqui, denota amor incondicional e sacrificial. "
    "Ela nos convida a enxergar o mundo com os olhos de Deus, com compaixão e entrega. "
    "Aplicar essa verdade significa amar sem limite, mesmo quando parece impossível. "
    "É o chamado mais alto da fé cristã: refletir esse amor em cada relacionamento."
)

FALLBACK_TEXT = (
    "Leia o texto novamente com atenção e observe o que ele revela sobre o caráter de Deus."
)

DEFAULT_KWARGS = dict(
    app_name="O Profeta",
    instagram_handle="@oprofeta.oficial",
    telegram_handle="@profeta_oficial_bot",
    telegram_link="t.me/profeta_oficial_bot",
    cta_line="Receba sua Palavra diária no Telegram",
)

REFLECTION_TEXT = (
    "Este versículo nos convida a contemplar a profundidade do amor que move o universo. "
    "Na quietude da oração, percebemos que somos amados antes mesmo de qualquer mérito. "
    "Deixe essa verdade pousar no seu coração hoje, como semente que germina em silêncio."
)

PRAYER_TEXT = (
    "Senhor, obrigado pelo amor imenso revelado nessas palavras. "
    "Que eu possa receber essa graça com coração aberto e grato. "
    "Que cada passo do meu dia reflita a luz do Teu amor incomparável. Amém."
)


# ── Autouse: always disable OpenAI so tests use Pillow fallback ───────────────

@pytest.fixture(autouse=True)
def disable_openai(monkeypatch):
    """Prevent any test from accidentally calling the real OpenAI API."""
    monkeypatch.setattr("app.share_service.OPENAI_IMAGE_ENABLED", False)


# ── is_shareable_content ──────────────────────────────────────────────────────

def test_shareable_content_accepts_good_text():
    assert is_shareable_content(LONG_EXPLANATION) is True


def test_shareable_content_accepts_verse_text():
    assert is_shareable_content("Porque Deus amou o mundo de tal maneira.") is True


def test_shareable_content_rejects_fallback_pattern():
    assert is_shareable_content(FALLBACK_TEXT) is False


def test_shareable_content_rejects_short_text():
    assert is_shareable_content("curto") is False
    assert is_shareable_content("") is False


def test_shareable_content_rejects_default_prayer():
    prayer = "Senhor, que a verdade de João 3:16 encontre espaço no meu coração hoje."
    assert is_shareable_content(prayer) is False


def test_shareable_content_rejects_observe_pattern():
    assert is_shareable_content("Observe o que ele revela sobre o caráter de Deus.") is False


# ── smart_truncate ─────────────────────────────────────────────────────────────

def test_smart_truncate_short_text_unchanged():
    text = "Texto curto."
    assert smart_truncate(text, max_chars=300) == text


def test_smart_truncate_cuts_at_sentence_boundary():
    text = "Primeira frase com sentido completo. Segunda frase que não deve aparecer aqui mesmo sendo longa."
    result = smart_truncate(text, max_chars=50)
    assert result.endswith(".")
    assert "Segunda" not in result


def test_smart_truncate_falls_back_to_word_boundary():
    text = "umapalavramuitolongasemespaco" * 20
    result = smart_truncate(text, max_chars=50)
    assert len(result) <= 54  # max_chars + "..."
    assert not result.endswith(" ")


def test_smart_truncate_adds_ellipsis_on_word_break():
    text = "palavra " * 80
    result = smart_truncate(text, max_chars=100)
    assert result.endswith("...")


def test_smart_truncate_long_explanation():
    result = smart_truncate(LONG_EXPLANATION, max_chars=200)
    assert len(result) <= 265
    assert result


# ── _content_hash ─────────────────────────────────────────────────────────────

def test_content_hash_deterministic():
    assert _content_hash("texto") == _content_hash("texto")


def test_content_hash_differs_for_different_text():
    assert _content_hash("texto A") != _content_hash("texto B")


def test_content_hash_length():
    assert len(_content_hash("qualquer coisa")) == 8


# ── _normalize_content ────────────────────────────────────────────────────────

def test_normalize_content_consistent():
    a = _normalize_content("verse", "  texto  com   espaços  ", "João 3:16")
    b = _normalize_content("verse", "texto com espaços", "João 3:16")
    assert a == b


def test_normalize_content_differs_by_type():
    a = _normalize_content("verse", "texto", "João 3:16")
    b = _normalize_content("explanation", "texto", "João 3:16")
    assert a != b


def test_normalize_content_differs_by_reference():
    a = _normalize_content("verse", "texto", "João 3:16")
    b = _normalize_content("verse", "texto", "Salmos 23:1")
    assert a != b


def test_normalize_content_none_reference():
    a = _normalize_content("verse", "texto", None)
    b = _normalize_content("verse", "texto", "")
    assert a == b


# ── _compute_content_hash ─────────────────────────────────────────────────────

def test_compute_content_hash_deterministic():
    n = _normalize_content("verse", "texto", "ref")
    assert _compute_content_hash(n) == _compute_content_hash(n)


def test_compute_content_hash_differs_for_different_content():
    a = _compute_content_hash(_normalize_content("verse", "texto A", "ref"))
    b = _compute_content_hash(_normalize_content("verse", "texto B", "ref"))
    assert a != b


def test_compute_content_hash_length():
    assert len(_compute_content_hash("any string")) == 16


# ── _card_path ────────────────────────────────────────────────────────────────

def test_card_path_format():
    path = _card_path("verse", VERSE, "conteudo de teste")
    assert path.parent == CARDS_DIR
    assert path.suffix == ".png"
    assert path.name.startswith("verse_joao_")


def test_card_path_stable_for_same_content():
    p1 = _card_path("verse", VERSE, "mesmo texto")
    p2 = _card_path("verse", VERSE, "mesmo texto")
    assert p1 == p2


def test_card_path_differs_for_different_content():
    p1 = _card_path("verse", VERSE, "texto A")
    p2 = _card_path("verse", VERSE, "texto B")
    assert p1 != p2


# ── _enforce_daily_limit ──────────────────────────────────────────────────────

def test_enforce_daily_limit_allows_first_generation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    assert _enforce_daily_limit("user_123", "verse") is True


def test_enforce_daily_limit_blocks_after_generation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from app.share_service import _mark_daily_generated
    _mark_daily_generated("user_123", "verse", tmp_path / "card.png")
    assert _enforce_daily_limit("user_123", "verse") is False


def test_enforce_daily_limit_independent_per_type(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from app.share_service import _mark_daily_generated
    _mark_daily_generated("user_123", "verse", tmp_path / "card.png")
    assert _enforce_daily_limit("user_123", "explanation") is True
    assert _enforce_daily_limit("user_123", "reflection") is True
    assert _enforce_daily_limit("user_123", "prayer") is True


# ── generate_share_card ───────────────────────────────────────────────────────

def test_generate_share_card_returns_none_for_fallback():
    card = generate_share_card("verse", VERSE, FALLBACK_TEXT, **DEFAULT_KWARGS)
    assert card is None


def test_generate_share_card_creates_png(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)

    assert card is not None
    assert isinstance(card, ShareCard)
    assert card.path.exists()
    assert card.path.suffix == ".png"
    assert card.cache_hit is False


def test_generate_share_card_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card1 = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)
    card2 = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)

    assert card1 is not None
    assert card2 is not None
    assert card2.cache_hit is True
    assert card1.path == card2.path


def test_generate_share_card_different_types_different_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card_verse = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)
    card_expl = generate_share_card("explanation", VERSE, LONG_EXPLANATION, **DEFAULT_KWARGS)

    assert card_verse is not None
    assert card_expl is not None
    assert card_verse.path != card_expl.path


def test_generate_share_card_explanation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card = generate_share_card("explanation", VERSE, LONG_EXPLANATION, **DEFAULT_KWARGS)

    assert card is not None
    assert "explanation" in card.path.name


def test_generate_share_card_produces_valid_png(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from PIL import Image

    card = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)
    assert card is not None

    img = Image.open(card.path)
    assert img.size == (1024, 1792)
    assert img.mode == "RGB"


def test_generate_share_card_returns_none_without_any_backend(monkeypatch, tmp_path):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    monkeypatch.setattr("app.share_service._PILLOW_AVAILABLE", False)
    # OPENAI_IMAGE_ENABLED is already False from autouse fixture

    card = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)
    assert card is None


# ── Global cache ──────────────────────────────────────────────────────────────

def test_global_cache_shared_across_users(tmp_path, monkeypatch):
    """Two users requesting the same content get the same cached image."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card_a = generate_share_card("verse", VERSE, VERSE["text"], user_id="user_A", **DEFAULT_KWARGS)
    card_b = generate_share_card("verse", VERSE, VERSE["text"], user_id="user_B", **DEFAULT_KWARGS)

    assert card_a is not None
    assert card_b is not None
    assert card_b.cache_hit is True
    assert card_a.path == card_b.path


# ── Daily user cache ──────────────────────────────────────────────────────────

def test_daily_cache_hit_returns_same_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card1 = generate_share_card("verse", VERSE, VERSE["text"], user_id="u1", **DEFAULT_KWARGS)
    card2 = generate_share_card("verse", VERSE, VERSE["text"], user_id="u1", **DEFAULT_KWARGS)

    assert card1 is not None
    assert card2 is not None
    assert card2.cache_hit is True


# ── Rate limit ────────────────────────────────────────────────────────────────

def test_rate_limit_returns_daily_cached_image_for_different_content(tmp_path, monkeypatch):
    """Same user, same type, different content → daily cache returns first image (not blocked)."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card1 = generate_share_card("verse", VERSE, VERSE["text"], user_id="u1", **DEFAULT_KWARGS)
    assert card1 is not None

    verse2 = dict(VERSE, verse="17")
    other_text = (
        "Porque Deus não enviou o seu Filho ao mundo para condenar o mundo, "
        "mas para que o mundo fosse salvo por ele. Esta é a promessa de graça."
    )
    card2 = generate_share_card("verse", verse2, other_text, user_id="u1", **DEFAULT_KWARGS)
    # Returns the same cached image from first generation (not None)
    assert card2 is not None
    assert card2.cache_hit is True
    assert card2.path == card1.path


def test_rate_limit_independent_across_types(tmp_path, monkeypatch):
    """Rate limit is per type — one type exhausted does not block others."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    generate_share_card("verse", VERSE, VERSE["text"], user_id="u1", **DEFAULT_KWARGS)

    card_expl = generate_share_card(
        "explanation", VERSE, LONG_EXPLANATION, user_id="u1", **DEFAULT_KWARGS
    )
    assert card_expl is not None


def test_no_rate_limit_without_user_id(tmp_path, monkeypatch):
    """Calls without user_id are never rate-limited."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    verse2 = dict(VERSE, verse="17")
    other_text = (
        "Porque Deus não enviou o seu Filho ao mundo para condenar o mundo, "
        "mas para que o mundo fosse salvo por ele. Esta é a promessa de graça."
    )
    card1 = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)
    card2 = generate_share_card("verse", verse2, other_text, **DEFAULT_KWARGS)

    assert card1 is not None
    assert card2 is not None


# ── OpenAI fallback ───────────────────────────────────────────────────────────

def test_openai_success_uses_returned_bytes(tmp_path, monkeypatch):
    """When OpenAI returns bytes, they are written to the global cache."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from PIL import Image
    import io

    # Build a minimal valid 1024x1792 PNG in memory.
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1792), color=(10, 16, 40)).save(buf, "PNG")
    fake_png = buf.getvalue()

    monkeypatch.setattr("app.share_service.OPENAI_IMAGE_ENABLED", True)
    monkeypatch.setattr(
        "app.share_service._generate_image_via_openai",
        lambda prompt: fake_png,
    )

    card = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)

    assert card is not None
    assert card.path.read_bytes() == fake_png


def test_openai_failure_falls_back_to_pillow(tmp_path, monkeypatch):
    """When OpenAI returns None, Pillow produces a valid card."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    monkeypatch.setattr("app.share_service.OPENAI_IMAGE_ENABLED", True)
    monkeypatch.setattr(
        "app.share_service._generate_image_via_openai",
        lambda prompt: None,
    )
    from PIL import Image

    card = generate_share_card("verse", VERSE, VERSE["text"], **DEFAULT_KWARGS)

    assert card is not None
    img = Image.open(card.path)
    assert img.size == (1024, 1792)


# ── Overflow / safe-area hardening ────────────────────────────────────────────

VERY_LONG_TEXT = (
    "O amor de Deus se manifesta de muitas formas ao longo das Escrituras sagradas. "
    "Cada página revela um aspecto diferente da sua graça e misericórdia incomparável. "
    "Do Gênesis ao Apocalipse, vemos um Deus que persiste em amar a sua criação amada. "
    "A história de Israel é a história de um amor que não desiste nunca, jamais, nunca. "
    "Os profetas proclamaram com toda clareza: o amor do Senhor dura para sempre, sem fim. "
    "Jesus veio para mostrar esse amor em forma humana, tangível, real e completamente presente. "
    "Cada milagre foi uma expressão desse amor incondicional e eterno para com todos nós. "
    "A cruz foi o ápice, o momento em que tudo foi finalmente revelado de forma completa. "
    "Ele ressurgiu para confirmar: o amor de Deus vence até a morte, sempre e eternamente."
)


def test_very_long_text_produces_valid_card(tmp_path, monkeypatch):
    """Text exceeding smart_truncate budget must not overflow the canvas."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from PIL import Image

    card = generate_share_card("explanation", VERSE, VERY_LONG_TEXT, **DEFAULT_KWARGS)

    assert card is not None
    img = Image.open(card.path)
    assert img.size == (1024, 1792)
    assert img.mode == "RGB"


def test_very_long_text_unique_path_from_short_text(tmp_path, monkeypatch):
    """Different content length must produce a different cache path."""
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card_long = generate_share_card("explanation", VERSE, VERY_LONG_TEXT, **DEFAULT_KWARGS)
    card_short = generate_share_card("explanation", VERSE, LONG_EXPLANATION, **DEFAULT_KWARGS)

    assert card_long is not None
    assert card_short is not None
    assert card_long.path != card_short.path


# ── Reflection and prayer card types ──────────────────────────────────────────

def test_generate_share_card_reflection(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card = generate_share_card("reflection", VERSE, REFLECTION_TEXT, **DEFAULT_KWARGS)

    assert card is not None
    assert "reflection" in card.path.name


def test_generate_share_card_prayer(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    card = generate_share_card("prayer", VERSE, PRAYER_TEXT, **DEFAULT_KWARGS)

    assert card is not None
    assert "prayer" in card.path.name


def test_generate_share_card_all_types_produce_different_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)

    cards = {
        t: generate_share_card(t, VERSE, text, **DEFAULT_KWARGS)
        for t, text in [
            ("verse", VERSE["text"]),
            ("explanation", LONG_EXPLANATION),
            ("reflection", REFLECTION_TEXT),
            ("prayer", PRAYER_TEXT),
        ]
    }

    assert all(c is not None for c in cards.values())
    paths = [c.path for c in cards.values()]
    assert len(set(paths)) == 4  # all distinct


def test_reflection_and_prayer_produce_valid_png(tmp_path, monkeypatch):
    monkeypatch.setattr("app.share_service.CARDS_DIR", tmp_path)
    from PIL import Image

    for card_type, text in [("reflection", REFLECTION_TEXT), ("prayer", PRAYER_TEXT)]:
        card = generate_share_card(card_type, VERSE, text, **DEFAULT_KWARGS)
        assert card is not None
        img = Image.open(card.path)
        assert img.size == (1024, 1792)
        assert img.mode == "RGB"


def test_shareable_content_accepts_reflection_text():
    assert is_shareable_content(REFLECTION_TEXT) is True


def test_shareable_content_accepts_prayer_text():
    assert is_shareable_content(PRAYER_TEXT) is True


# ── _build_image_prompt ───────────────────────────────────────────────────────

_REF = "João 3:16"

_QUALITY_MARKERS = [
    "EXTREMELY premium",
    "Clean and minimalist",
    "High perceived value",
    "Perfect typographic hierarchy",
    "Zero visual noise",
    "No photographs",
]

_BRANDING_MARKERS = [
    "PROFETA",          # watermark text
    "10 to 15 percent", # watermark opacity
    "VERY LARGE",       # watermark size
    "footer",           # footer section keyword (case-insensitive matched below)
    "@profeta_oficial_bot",
    "@oprofeta.oficial",
]

_BG_MARKERS = [
    "#0A1028",
    "#102A5C",
    "#173B72",
    "vignette",
]


def test_prompt_verse_contains_label():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    assert "VERSÍCULO DO DIA" in p


def test_prompt_explanation_contains_label():
    p = _build_image_prompt("explanation", LONG_EXPLANATION, _REF)
    assert "EXPLICAÇÃO" in p
    assert "EXPLICAÇÃO DO DIA" not in p


def test_prompt_reflection_contains_label():
    p = _build_image_prompt("reflection", REFLECTION_TEXT, None)
    assert "REFLEXÃO" in p
    assert "REFLEXÃO DO DIA" not in p


def test_prompt_prayer_contains_label():
    p = _build_image_prompt("prayer", PRAYER_TEXT, None)
    assert "ORAÇÃO" in p
    assert "ORAÇÃO DO DIA" not in p


def test_prompt_contains_main_text():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    assert VERSE["text"] in p


def test_prompt_contains_background_spec():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    for marker in _BG_MARKERS:
        assert marker in p, f"Background marker missing: {marker!r}"


def test_prompt_contains_quality_requirements():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    for marker in _QUALITY_MARKERS:
        assert marker in p, f"Quality marker missing: {marker!r}"


def test_prompt_contains_branding():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    for marker in _BRANDING_MARKERS:
        assert marker in p, f"Branding marker missing: {marker!r}"


def test_prompt_verse_includes_prominent_reference():
    p = _build_image_prompt("verse", VERSE["text"], _REF)
    assert _REF in p
    assert "prominent" in p.lower() or "gold #D4AF37" in p


def test_prompt_explanation_includes_secondary_reference():
    p = _build_image_prompt("explanation", LONG_EXPLANATION, _REF)
    assert _REF in p


def test_prompt_reflection_excludes_reference():
    p = _build_image_prompt("reflection", REFLECTION_TEXT, _REF)
    assert _REF not in p


def test_prompt_prayer_excludes_reference():
    p = _build_image_prompt("prayer", PRAYER_TEXT, _REF)
    assert _REF not in p


def test_prompts_differ_by_type():
    types_and_texts = [
        ("verse",       VERSE["text"]),
        ("explanation", LONG_EXPLANATION),
        ("reflection",  REFLECTION_TEXT),
        ("prayer",      PRAYER_TEXT),
    ]
    prompts = [_build_image_prompt(t, txt, _REF) for t, txt in types_and_texts]
    assert len(set(prompts)) == 4, "Every card type must produce a distinct prompt"


def test_prompt_tone_varies_by_type():
    verse_p   = _build_image_prompt("verse",      VERSE["text"],   _REF)
    prayer_p  = _build_image_prompt("prayer",     PRAYER_TEXT,     None)
    assert "impactante" in verse_p
    assert "íntimo" in prayer_p


def test_prompt_contains_cta_line():
    p = _build_image_prompt("verse", VERSE["text"], _REF, cta_line="Receba sua Palavra diária no Telegram")
    assert "Receba sua Palavra diária no Telegram" in p


def test_prompt_uses_provided_branding_params():
    p = _build_image_prompt(
        "verse", VERSE["text"], _REF,
        app_name="Meu App",
        instagram_handle="@meuapp",
        telegram_handle="@meuapp_bot",
    )
    assert "Meu App" in p
    assert "@meuapp" in p
    assert "@meuapp_bot" in p
