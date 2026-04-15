from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import User, UserJourney, UserPreference, UserThemeInterest, Verse, VerseHistory


@pytest.mark.asyncio
async def test_verse_service_avoids_recent_repetition(db_sessionmaker, monkeypatch):
    import app.verse_service as verse_service

    async with db_sessionmaker() as session:
        session.add_all(
            [
                Verse(book="Salmos", chapter=23, verse=1, text="A", reference="Salmos 23:1"),
                Verse(book="Romanos", chapter=8, verse=28, text="B", reference="Romanos 8:28"),
            ]
        )
        session.add(
            VerseHistory(
                telegram_user_id="u1",
                book="Salmos",
                chapter="23",
                verse="1",
                text="A",
            )
        )
        await session.commit()

    offsets = iter([0, 1])
    monkeypatch.setattr(verse_service.random, "randint", lambda start, end: next(offsets))

    verse = await verse_service.get_random_verse_for_user("u1")

    assert verse["book"] == "Romanos"
    assert verse["verse"] == "28"


def test_verse_text_and_tts_rendering(sample_verse):
    import app.verse_service as verse_service

    text = verse_service.format_verse_text(sample_verse, journey_title="Fe")
    tts = verse_service.build_tts_text(sample_verse)

    assert "Trilha ativa: Fe" in text
    assert "Salmos 23:1" in text
    assert "Versículo do dia" in tts


@pytest.mark.asyncio
async def test_audio_service_cache_miss_then_hit(tmp_audio_dirs, monkeypatch, sample_verse):
    import app.audio_service as audio_service

    audio_dir, cache_dir = tmp_audio_dirs
    source_path = audio_dir / "source.mp3"
    source_path.write_bytes(b"fake-audio")

    async def fake_get_or_create_tts_audio(text):
        return audio_service.AudioAsset(key="source", path=source_path, cache_hit=False)

    monkeypatch.setattr(audio_service, "get_or_create_tts_audio", fake_get_or_create_tts_audio)

    first_asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, "texto de teste")
    second_asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, "texto de teste")

    assert first_asset.cache_hit is False
    assert second_asset.cache_hit is True
    assert (cache_dir / "versiculo_Salmos_23_1.mp3").exists()


@pytest.mark.asyncio
async def test_content_service_generates_structured_reflection(monkeypatch, sample_verse):
    import app.content_service as content_service

    class FakeResponse:
        output_text = '{"explanation":"Essencia","context":"Contexto","application":"Aplicacao","prayer":"Oracao","summary":"Resumo"}'

    class FakeClient:
        def __init__(self):
            self.responses = self

        def create(self, model, input):
            return FakeResponse()

    monkeypatch.setattr(content_service, "OpenAI", FakeClient)

    reflection = await content_service.generate_reflection_content(sample_verse, depth="balanced")

    assert reflection.explanation == "Essencia"
    assert reflection.prayer == "Oracao"


@pytest.mark.asyncio
async def test_content_service_falls_back_for_invalid_json(monkeypatch, sample_verse):
    import app.content_service as content_service

    class FakeResponse:
        output_text = "texto livre sem json"

    class FakeClient:
        def __init__(self):
            self.responses = self

        def create(self, model, input):
            return FakeResponse()

    monkeypatch.setattr(content_service, "OpenAI", FakeClient)

    reflection = await content_service.generate_reflection_content(sample_verse, depth="short")

    assert reflection.explanation == "texto livre sem json"
    assert reflection.prayer


@pytest.mark.asyncio
async def test_subscription_service_checks_inactive_and_active(db_sessionmaker):
    import app.subscription_service as subscription_service

    user = await subscription_service.get_or_create_user("u2", telegram_username="qa")

    assert await subscription_service.user_has_active_subscription("u2") is False

    activated = await subscription_service.activate_subscription_for_user(user_id=user.id)

    assert activated.status == "active"
    assert await subscription_service.user_has_active_subscription("u2") is True


@pytest.mark.asyncio
async def test_token_service_create_validate_and_consume(db_sessionmaker):
    import app.token_service as token_service

    token = await token_service.create_activation_token()
    row = await token_service.validate_activation_token(token)
    first = await token_service.consume_activation_token(token, "u3")
    second = await token_service.consume_activation_token(token, "u3")

    assert row is not None
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_payment_service_is_idempotent(db_sessionmaker, monkeypatch):
    import app.payment_service as payment_service

    monkeypatch.setattr(payment_service, "generate_token_value", lambda: "fixed-token")

    first = await payment_service.create_token_for_paid_event("pay_1", "cus_1", "plink_1")
    second = await payment_service.create_token_for_paid_event("pay_1", "cus_1", "plink_1")

    assert first == "fixed-token"
    assert second is None


@pytest.mark.asyncio
async def test_journey_service_start_and_continue(db_sessionmaker):
    import app.journey_service as journey_service

    journey = await journey_service.start_journey(db_sessionmaker, "u4", "fe")
    active = await journey_service.get_active_journey(db_sessionmaker, "u4")
    touchpoint = await journey_service.build_active_journey_touchpoint(db_sessionmaker, "u4")

    assert journey is not None
    assert active is not None
    assert active.key == "fe"
    assert "Trilha: Fé" in touchpoint

    async with db_sessionmaker() as session:
        row = await session.scalar(select(UserJourney))
    assert row.current_step == 1


@pytest.mark.asyncio
async def test_user_profile_service_saves_favorites_and_theme_interest(db_sessionmaker, sample_verse):
    import app.user_profile_service as user_profile_service

    added = await user_profile_service.add_favorite_verse(db_sessionmaker, "u5", sample_verse)
    favorites = await user_profile_service.list_recent_favorites(db_sessionmaker, "u5")
    await user_profile_service.record_theme_interest(db_sessionmaker, "u5", "fe", source="journey")
    preference = await user_profile_service.get_or_create_user_preference(db_sessionmaker, "u5")

    assert added is True
    assert favorites == ["Salmos 23:1"]
    assert preference.last_requested_theme == "fe"

    async with db_sessionmaker() as session:
        theme_interest = await session.scalar(select(UserThemeInterest))
        user = await session.scalar(select(User).where(User.telegram_user_id == "u5"))
        stored_preference = await session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))

    assert theme_interest is not None
    assert stored_preference.favorite_themes == "fe"
