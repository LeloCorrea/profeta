from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Subscription, User, UserJourney, UserPreference, UserThemeInterest, Verse, VerseExplanation, VerseHistory


@pytest.mark.asyncio
async def test_verse_service_avoids_recent_repetition(db_sessionmaker):
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

    audio_dir = tmp_audio_dirs

    async def fake_save_tts_audio(path, text):
        path.write_bytes(b"fake-audio")

    monkeypatch.setattr(audio_service, "_save_tts_audio", fake_save_tts_audio)

    first_asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, "texto de teste")
    second_asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, "texto de teste")

    assert first_asset.cache_hit is False
    assert second_asset.cache_hit is True
    assert (audio_dir / "versiculo_salmos_23_1.mp3").exists()


@pytest.mark.asyncio
async def test_audio_service_normalizes_accents_in_filename(tmp_audio_dirs, monkeypatch):
    import app.audio_service as audio_service

    audio_dir = tmp_audio_dirs
    verse = {
        "book": "Juízes",
        "chapter": "6",
        "verse": "12",
        "text": "Teste",
    }

    async def fake_save_tts_audio(path, text):
        path.write_bytes(b"fake-audio")

    monkeypatch.setattr(audio_service, "_save_tts_audio", fake_save_tts_audio)

    asset = await audio_service.ensure_named_audio_asset("explicacao", verse, "texto")

    assert asset.path == audio_dir / "explicacao_juizes_6_12.mp3"


@pytest.mark.asyncio
async def test_content_service_generates_structured_reflection(monkeypatch, sample_verse):
    import app.content_service as content_service

    class FakeMessage:
        content = '{"explanation":"Essencia","context":"Contexto","application":"Aplicacao","prayer":"Oracao","summary":"Resumo"}'

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletionsResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, model, messages, max_tokens=None, temperature=None):
            return FakeCompletionsResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_service, "_get_openai_client", lambda: FakeClient())

    reflection = await content_service.generate_reflection_content(sample_verse, depth="balanced")

    assert reflection.explanation == "Essencia"
    assert reflection.prayer == "Oracao"
    assert reflection.is_fallback is False


@pytest.mark.asyncio
async def test_content_service_falls_back_for_invalid_json(monkeypatch, sample_verse):
    import app.content_service as content_service

    class FakeMessage:
        content = "texto livre sem json"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletionsResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, model, messages, max_tokens=None, temperature=None):
            return FakeCompletionsResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_service, "_get_openai_client", lambda: FakeClient())

    reflection = await content_service.generate_reflection_content(sample_verse, depth="short")

    assert reflection.is_fallback is True
    assert reflection.prayer


@pytest.mark.asyncio
async def test_content_service_persists_and_reuses_explanation(db_sessionmaker, monkeypatch, sample_verse):
    import app.content_service as content_service

    calls = {"count": 0}

    async def fake_generate_reflection_content(verse, depth="balanced", journey_title=None):
        calls["count"] += 1
        return content_service.ReflectionContent(
            explanation="Explicacao persistida com detalhes suficientes para passar na validacao de cache que exige ao menos cem caracteres no texto.",
            context="Contexto original.",
            application="Aplicacao original.",
            prayer="Oracao original.",
            summary="Resumo original.",
            depth=depth,
        )

    monkeypatch.setattr(content_service, "generate_reflection_content", fake_generate_reflection_content)

    first = await content_service.get_or_create_reflection_content(
        db_sessionmaker,
        "u-cache",
        sample_verse,
        depth="balanced",
    )
    second = await content_service.get_or_create_reflection_content(
        db_sessionmaker,
        "u-cache",
        sample_verse,
        depth="balanced",
    )

    assert calls["count"] == 1
    long_explanation = "Explicacao persistida com detalhes suficientes para passar na validacao de cache que exige ao menos cem caracteres no texto."
    assert first.explanation == long_explanation
    assert second.explanation == long_explanation

    async with db_sessionmaker() as session:
        items = (
            await session.execute(
                select(VerseExplanation).where(
                    VerseExplanation.book == "Salmos",
                    VerseExplanation.chapter == "23",
                    VerseExplanation.verse == "1",
                )
            )
        ).scalars().all()

    assert len(items) == 1
    assert items[0].explanation == long_explanation


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


@pytest.mark.asyncio
async def test_subscription_service_expires_overdue_subscriptions(db_sessionmaker):
    import app.subscription_service as subscription_service

    past = datetime.utcnow() - timedelta(days=1)
    future = datetime.utcnow() + timedelta(days=30)

    async with db_sessionmaker() as session:
        expired_user = User(telegram_user_id="u-expired", status="active")
        active_user = User(telegram_user_id="u-active", status="active")
        session.add_all([expired_user, active_user])
        await session.flush()

        session.add_all([
            Subscription(user_id=expired_user.id, plan_name="monthly", status="active", paid_until=past),
            Subscription(user_id=active_user.id, plan_name="monthly", status="active", paid_until=future),
        ])
        await session.commit()

    count = await subscription_service.expire_overdue_subscriptions()

    assert count == 1

    async with db_sessionmaker() as session:
        subs = (await session.execute(select(Subscription))).scalars().all()
        by_user = {s.user_id: s for s in subs}
        expired_id = (await session.scalar(select(User).where(User.telegram_user_id == "u-expired"))).id
        active_id = (await session.scalar(select(User).where(User.telegram_user_id == "u-active"))).id

    assert by_user[expired_id].status == "inactive"
    assert by_user[active_id].status == "active"


@pytest.mark.asyncio
async def test_token_service_sets_used_at_on_consume(db_sessionmaker):
    import app.token_service as token_service

    token = await token_service.create_activation_token()
    await token_service.consume_activation_token(token, "u-used-at")

    async with db_sessionmaker() as session:
        from app.models import ActivationToken
        row = await session.scalar(select(ActivationToken).where(ActivationToken.token == token))

    assert row.used_at is not None
    assert isinstance(row.used_at, datetime)


# ── Fase 3: rate limiter ──────────────────────────────────────────────────────

def test_rate_limiter_allows_within_limit_and_blocks_when_exceeded():
    from app.rate_limiter import check_rate_limit, reset_rate_limit

    key = "test-user:versiculo"
    reset_rate_limit(key)

    assert check_rate_limit(key, max_calls=3, window_seconds=60) is True
    assert check_rate_limit(key, max_calls=3, window_seconds=60) is True
    assert check_rate_limit(key, max_calls=3, window_seconds=60) is True
    assert check_rate_limit(key, max_calls=3, window_seconds=60) is False


def test_rate_limiter_independent_keys_do_not_interfere():
    from app.rate_limiter import check_rate_limit, reset_rate_limit

    reset_rate_limit("user-a:cmd")
    reset_rate_limit("user-b:cmd")

    for _ in range(3):
        check_rate_limit("user-a:cmd", max_calls=3, window_seconds=60)

    assert check_rate_limit("user-b:cmd", max_calls=3, window_seconds=60) is True


# ── Fase 3: audio cleanup ─────────────────────────────────────────────────────

def test_audio_cleanup_removes_old_files_and_keeps_recent(tmp_audio_dirs, monkeypatch):
    import os
    import time
    import app.audio_service as audio_service

    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_audio_dirs)

    old_file = tmp_audio_dirs / "old_audio.mp3"
    new_file = tmp_audio_dirs / "new_audio.mp3"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")

    old_time = time.time() - (8 * 24 * 3600)
    os.utime(old_file, (old_time, old_time))

    count = audio_service.cleanup_old_audio_files(max_age_days=7)

    assert count == 1
    assert not old_file.exists()
    assert new_file.exists()


# ── Fase 3: verse search ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verse_service_searches_by_keyword_in_db(db_sessionmaker):
    import app.verse_service as verse_service

    async with db_sessionmaker() as session:
        session.add_all([
            Verse(book="Salmos", chapter=23, verse=1, text="O Senhor e meu pastor e nada me faltara", reference="Salmos 23:1"),
            Verse(book="Romanos", chapter=8, verse=28, text="Todas as coisas cooperam para o bem", reference="Romanos 8:28"),
        ])
        await session.commit()

    results = await verse_service.search_verses_by_keyword("pastor", limit=5)
    assert len(results) == 1
    assert results[0]["book"] == "Salmos"

    empty = await verse_service.search_verses_by_keyword("palavrainexistentexyz", limit=5)
    assert empty == []


# ── Fase 3: admin stats ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_stats_returns_correct_counts(db_sessionmaker):
    import app.subscription_service as subscription_service

    user1 = await subscription_service.get_or_create_user("u-admin-1")
    user2 = await subscription_service.get_or_create_user("u-admin-2")
    await subscription_service.activate_subscription_for_user(user_id=user1.id)

    stats = await subscription_service.get_admin_stats()

    assert stats["total_users"] >= 2
    assert stats["active_subscriptions"] >= 1


@pytest.mark.asyncio
async def test_admin_recent_users_lists_active_subscribers(db_sessionmaker):
    import app.subscription_service as subscription_service

    user = await subscription_service.get_or_create_user("u-admin-list", telegram_username="qa_admin")
    await subscription_service.activate_subscription_for_user(user_id=user.id)

    users = await subscription_service.get_admin_recent_users(limit=10)

    assert any(u["telegram_user_id"] == "u-admin-list" for u in users)


@pytest.mark.asyncio
async def test_activation_sets_paid_until_30_days(db_sessionmaker):
    import app.subscription_service as subscription_service
    from sqlalchemy import select
    from app.models import Subscription

    user = await subscription_service.get_or_create_user("u-paid-until")
    sub = await subscription_service.activate_subscription_for_user(user_id=user.id)

    assert sub.paid_until is not None
    remaining = (sub.paid_until - datetime.utcnow()).days
    assert 28 <= remaining <= 30


@pytest.mark.asyncio
async def test_renewal_extends_paid_until(db_sessionmaker):
    import app.subscription_service as subscription_service
    from sqlalchemy import select
    from app.models import Subscription

    user = await subscription_service.get_or_create_user("u-renewal")
    first = await subscription_service.activate_subscription_for_user(user_id=user.id)
    first_expiry = first.paid_until

    second = await subscription_service.activate_subscription_for_user(user_id=user.id)

    assert second.paid_until > first_expiry
    remaining = (second.paid_until - datetime.utcnow()).days
    assert remaining >= 58


@pytest.mark.asyncio
async def test_subscription_info_returns_active_plan(db_sessionmaker):
    import app.subscription_service as subscription_service

    user = await subscription_service.get_or_create_user("u-info")
    await subscription_service.activate_subscription_for_user(user_id=user.id)

    info = await subscription_service.get_subscription_info("u-info")

    assert info["has_account"] is True
    assert info["has_subscription"] is True
    assert info["status"] == "active"
    assert info["paid_until"] is not None
    assert info["days_remaining"] >= 28


@pytest.mark.asyncio
async def test_subscription_info_returns_no_account_for_unknown(db_sessionmaker):
    import app.subscription_service as subscription_service

    info = await subscription_service.get_subscription_info("u-nobody")

    assert info["has_account"] is False
