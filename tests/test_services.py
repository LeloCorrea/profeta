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

    async def fake_save_tts_audio(path, text, cfg=None):
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

    async def fake_save_tts_audio(path, text, cfg=None):
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

    reflection = await content_service.generate_explanation_content(sample_verse)

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

    reflection = await content_service.generate_explanation_content(sample_verse)

    assert reflection.is_fallback is True
    assert reflection.prayer


@pytest.mark.asyncio
async def test_content_service_persists_and_reuses_explanation(db_sessionmaker, monkeypatch, sample_verse):
    import app.content_service as content_service

    calls = {"count": 0}

    async def fake_generate_explanation_content(verse, journey_title=None, cfg=None):
        calls["count"] += 1
        return content_service.ReflectionContent(
            explanation="Explicacao persistida com detalhes suficientes para passar na validacao de cache que exige ao menos cem caracteres no texto.",
            context="Contexto original.",
            application="Aplicacao original.",
            prayer="Oracao original.",
            summary="Resumo original.",
            depth="balanced",
        )

    monkeypatch.setattr(content_service, "generate_explanation_content", fake_generate_explanation_content)

    first = await content_service.get_or_create_explanation_content(
        db_sessionmaker,
        "u-cache",
        sample_verse,
    )
    second = await content_service.get_or_create_explanation_content(
        db_sessionmaker,
        "u-cache",
        sample_verse,
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

    count, expired_ids = await subscription_service.expire_overdue_subscriptions()

    assert count == 1
    assert "u-expired" in expired_ids

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


# ── is_fallback: campo real no dataclass ──────────────────────────────────────

# ── Fix #4: paid_until verificado em tempo real ───────────────────────────────

@pytest.mark.asyncio
async def test_user_has_active_subscription_rejects_expired_paid_until(db_sessionmaker):
    """Status='active' com paid_until no passado → False. Job diário não pode ser a única barreira."""
    import app.subscription_service as subscription_service

    past = datetime.utcnow() - timedelta(days=1)

    async with db_sessionmaker() as session:
        user = User(telegram_user_id="u-paid-expired", status="active")
        session.add(user)
        await session.flush()
        session.add(Subscription(
            user_id=user.id,
            plan_name="monthly",
            status="active",
            paid_until=past,
        ))
        await session.commit()

    result = await subscription_service.user_has_active_subscription("u-paid-expired")
    assert result is False


@pytest.mark.asyncio
async def test_user_has_active_subscription_accepts_valid_paid_until(db_sessionmaker):
    """Status='active' com paid_until no futuro → True."""
    import app.subscription_service as subscription_service

    future = datetime.utcnow() + timedelta(days=15)

    async with db_sessionmaker() as session:
        user = User(telegram_user_id="u-paid-valid", status="active")
        session.add(user)
        await session.flush()
        session.add(Subscription(
            user_id=user.id,
            plan_name="monthly",
            status="active",
            paid_until=future,
        ))
        await session.commit()

    result = await subscription_service.user_has_active_subscription("u-paid-valid")
    assert result is True


@pytest.mark.asyncio
async def test_get_active_user_ids_excludes_expired_paid_until(db_sessionmaker, monkeypatch):
    """Job diário não envia versículo para assinaturas com paid_until vencido."""
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "SessionLocal", db_sessionmaker)

    past = datetime.utcnow() - timedelta(days=1)
    future = datetime.utcnow() + timedelta(days=15)

    async with db_sessionmaker() as session:
        valid_user = User(telegram_user_id="u-job-valid", status="active")
        expired_user = User(telegram_user_id="u-job-expired", status="active")
        session.add_all([valid_user, expired_user])
        await session.flush()
        session.add_all([
            Subscription(user_id=valid_user.id, plan_name="monthly", status="active", paid_until=future),
            Subscription(user_id=expired_user.id, plan_name="monthly", status="active", paid_until=past),
        ])
        await session.commit()

    user_ids = await jobs_module.get_active_user_ids()

    assert "u-job-valid" in user_ids
    assert "u-job-expired" not in user_ids


# ── Fix #5: save_verse_history isolado do retry loop ─────────────────────────

@pytest.mark.asyncio
async def test_verse_history_failure_does_not_retry_delivery(tmp_path, monkeypatch):
    """
    Se save_verse_history falha após entrega confirmada, a função deve retornar True
    sem retentar o envio. O usuário NÃO deve receber o mesmo versículo duas vezes.
    """
    import logging
    import app.jobs as jobs_module

    # Arquivo de áudio real necessário porque _send_verse_with_retry abre o path
    fake_audio = tmp_path / "verse.mp3"
    fake_audio.write_bytes(b"fake-audio-data")

    class FakeAudioAsset:
        path = fake_audio
        cache_hit = False

    send_counts = {"messages": 0, "audios": 0}

    class FakeBot:
        async def send_message(self, chat_id, text):
            send_counts["messages"] += 1

        async def send_audio(self, chat_id, audio, title):
            send_counts["audios"] += 1

    sample = {"book": "Salmos", "chapter": "23", "verse": "1", "text": "O Senhor é meu pastor."}

    async def fake_get_verse(uid):
        return sample

    async def fake_ensure_audio(*args, **kwargs):
        return FakeAudioAsset()

    async def broken_history(user_id, verse):
        raise RuntimeError("DB temporariamente indisponível")

    monkeypatch.setattr(jobs_module, "get_random_verse_for_user", fake_get_verse)
    monkeypatch.setattr(jobs_module, "ensure_named_audio_asset", fake_ensure_audio)
    monkeypatch.setattr(jobs_module, "build_tts_text", lambda v: "texto")
    monkeypatch.setattr(jobs_module, "save_verse_history", broken_history)

    logger = logging.getLogger("test_jobs_history")
    result = await jobs_module._send_verse_with_retry("u-hist-test", FakeBot(), logger, max_attempts=3)

    assert result is True               # entrega confirmada
    assert send_counts["messages"] == 1  # enviado exatamente uma vez
    assert send_counts["audios"] == 1    # sem retry duplicado


# ── Fix #6: job sem lock de concorrência ─────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_job_aborts_when_lock_already_held(monkeypatch):
    """Segunda instância do job deve abortar imediatamente sem enviar nada."""
    import app.jobs as jobs_module

    bot_created = {"value": False}
    original_bot = jobs_module.Bot if hasattr(jobs_module, "Bot") else None

    def no_lock():
        return None  # simula outra instância segurando o lock

    monkeypatch.setattr(jobs_module, "_acquire_job_lock", no_lock)

    # main() deve retornar sem levantar exceção e sem criar bot
    await jobs_module.main()

    # Se chegou aqui sem RuntimeError, a guarda de lock funcionou.
    # Verificação adicional: TELEGRAM_BOT_TOKEN está vazio em testes, então
    # se o lock não abortasse, main() levantaria RuntimeError na checagem de config.
    assert True


def test_acquire_job_lock_returns_handle_on_first_call(tmp_path, monkeypatch):
    """_acquire_job_lock deve retornar um file handle válido (não None) em execução normal."""
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "LOCK_FILE", tmp_path / "test.lock")

    lock = jobs_module._acquire_job_lock()

    assert lock is not None
    lock.close()


def test_reflection_content_is_fallback_is_a_proper_field(sample_verse):
    from app.content_service import ReflectionContent

    ok = ReflectionContent(
        explanation="x", context="x", application="x", prayer="x", summary="x",
        is_fallback=False,
    )
    fallback = ReflectionContent(
        explanation="x", context="x", application="x", prayer="x", summary="x",
        is_fallback=True,
    )
    assert ok.is_fallback is False
    assert fallback.is_fallback is True


def test_reflection_content_from_dict_preserves_is_fallback():
    from app.content_service import ReflectionContent

    r = ReflectionContent.from_dict({
        "explanation": "e", "context": "c", "application": "a",
        "prayer": "p", "summary": "s", "is_fallback": True,
    })
    assert r.is_fallback is True


def test_build_explanation_audio_text_empty_when_fallback(sample_verse):
    from app.content_service import ReflectionContent, build_explanation_audio_text

    fallback_reflection = ReflectionContent(
        explanation="x", context="x", application="x", prayer="x", summary="x",
        is_fallback=True,
    )
    assert build_explanation_audio_text(sample_verse, fallback_reflection) == ""


@pytest.mark.asyncio
async def test_send_reflection_audio_skips_when_fallback(
    tmp_audio_dirs, monkeypatch, fake_update, fake_context, sample_verse
):
    import app.bot_flows as bot_flows_module
    import app.audio_service as audio_service
    from app.content_service import ReflectionContent

    tts_called = {"count": 0}

    async def fake_save_tts(path, text, cfg=None):
        tts_called["count"] += 1

    monkeypatch.setattr(audio_service, "_save_tts_audio", fake_save_tts)

    fallback_reflection = ReflectionContent(
        explanation="Fallback texto.", context="x", application="x", prayer="x", summary="x",
        is_fallback=True,
    )
    message = fake_update.effective_message
    await bot_flows_module.send_reflection_audio(message, sample_verse, fallback_reflection)

    assert tts_called["count"] == 0
    assert message.audios == []


# ── Fix #7: proteção contra disco cheio no cache de áudio ────────────────────

@pytest.mark.asyncio
async def test_ensure_named_audio_asset_returns_none_when_disk_space_low(
    tmp_audio_dirs, monkeypatch, sample_verse
):
    """Quando espaço livre está abaixo do threshold, retorna None sem gerar áudio."""
    import app.audio_service as audio_service

    monkeypatch.setattr(audio_service, "audio_disk_space_ok", lambda: False)

    tts_called = {"count": 0}

    async def fake_save_tts(path, text, cfg=None):
        tts_called["count"] += 1

    monkeypatch.setattr(audio_service, "_save_tts_audio", fake_save_tts)

    asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, "texto")

    assert asset is None
    assert tts_called["count"] == 0


@pytest.mark.asyncio
async def test_ensure_named_audio_asset_skips_disk_check_on_cache_hit(
    tmp_audio_dirs, monkeypatch, sample_verse
):
    """Cache hit (arquivo + hash correto) não faz checagem de disco — sempre retorna o asset."""
    import app.audio_service as audio_service

    text = "texto"
    path = audio_service.build_named_audio_path("versiculo", sample_verse)
    path.write_bytes(b"cached-audio")
    audio_service._save_audio_hash(path, text)  # hash deve bater para ser cache hit

    disk_checked = {"count": 0}

    def counting_ok():
        disk_checked["count"] += 1
        return False  # seria rejeitado se chamado

    monkeypatch.setattr(audio_service, "audio_disk_space_ok", counting_ok)

    asset = await audio_service.ensure_named_audio_asset("versiculo", sample_verse, text)

    assert asset is not None
    assert asset.cache_hit is True
    assert disk_checked["count"] == 0  # disco não verificado para cache hit


@pytest.mark.asyncio
async def test_verse_sent_as_text_only_when_audio_space_guard_fires(tmp_path, monkeypatch):
    """Se ensure_named_audio_asset retorna None (disco cheio), versículo é enviado só como texto."""
    import logging
    import app.jobs as jobs_module

    send_counts = {"messages": 0, "audios": 0}

    class FakeBot:
        async def send_message(self, chat_id, text):
            send_counts["messages"] += 1

        async def send_audio(self, chat_id, audio, title):
            send_counts["audios"] += 1

    sample = {"book": "Salmos", "chapter": "23", "verse": "1", "text": "O Senhor é meu pastor."}

    async def fake_get_verse(uid):
        return sample

    async def no_audio(*args, **kwargs):
        return None  # guard de disco disparou

    async def fake_history(user_id, verse):
        pass

    monkeypatch.setattr(jobs_module, "get_random_verse_for_user", fake_get_verse)
    monkeypatch.setattr(jobs_module, "ensure_named_audio_asset", no_audio)
    monkeypatch.setattr(jobs_module, "build_tts_text", lambda v: "texto")
    monkeypatch.setattr(jobs_module, "save_verse_history", fake_history)

    logger = logging.getLogger("test_jobs_disk")
    result = await jobs_module._send_verse_with_retry("u-disk-test", FakeBot(), logger, max_attempts=1)

    assert result is True          # entrega confirmada (texto chegou)
    assert send_counts["messages"] == 1
    assert send_counts["audios"] == 0  # sem áudio — degradação graciosa


# ── Fix #8: race condition em consume_activation_token ───────────────────────

@pytest.mark.asyncio
async def test_consume_activation_token_records_first_consumer_not_second(db_sessionmaker):
    """UPDATE atômico garante que o primeiro consumidor é registrado; o segundo recebe False."""
    import app.token_service as token_service
    from app.models import ActivationToken
    from sqlalchemy import select

    token = await token_service.create_activation_token()

    first = await token_service.consume_activation_token(token, "primeiro-user")
    second = await token_service.consume_activation_token(token, "segundo-user")

    assert first is True
    assert second is False

    async with db_sessionmaker() as session:
        row = await session.scalar(
            select(ActivationToken).where(ActivationToken.token == token)
        )

    assert row.status == "used"
    assert row.telegram_user_id == "primeiro-user"  # segundo não sobrescreveu


@pytest.mark.asyncio
async def test_consume_activation_token_nonexistent_returns_false(db_sessionmaker):
    """Token inexistente retorna False imediatamente, sem erro."""
    import app.token_service as token_service

    result = await token_service.consume_activation_token("token-que-nao-existe", "u-qualquer")

    assert result is False


# ── Fix #10: asaas_customer_id desatualizado em recompras ────────────────────

@pytest.mark.asyncio
async def test_activate_with_payment_atomic_updates_changed_customer_id(db_sessionmaker):
    """Segunda compra com novo customer_id sobrescreve o antigo — find_telegram_user não quebra."""
    import app.payment_service as payment_service
    from app.models import User
    from sqlalchemy import select

    await payment_service.activate_with_payment_atomic("pay_cus_001", "cus_antigo", "888001001")
    await payment_service.activate_with_payment_atomic("pay_cus_002", "cus_novo", "888001001")

    async with db_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == "888001001"))

    assert user.asaas_customer_id == "cus_novo"


@pytest.mark.asyncio
async def test_activate_with_payment_atomic_sets_customer_id_on_new_user(db_sessionmaker):
    """Primeiro pagamento define o customer_id corretamente."""
    import app.payment_service as payment_service
    from app.models import User
    from sqlalchemy import select

    await payment_service.activate_with_payment_atomic("pay_cus_003", "cus_primeiro", "888002001")

    async with db_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == "888002001"))

    assert user.asaas_customer_id == "cus_primeiro"


@pytest.mark.asyncio
async def test_consume_token_updates_changed_customer_id(db_sessionmaker):
    """Se usuário já tem customer_id antigo, consumo do token com novo customer_id atualiza."""
    import app.token_service as token_service
    import app.payment_service as payment_service
    from app.models import ActivationToken, User
    from sqlalchemy import select

    # Cria usuário com customer_id antigo via pagamento direto
    await payment_service.activate_with_payment_atomic("pay_tok_001", "cus_antigo_tok", "888003001")

    # Cria token com novo customer_id (usuário criou novo cadastro no Asaas)
    async with db_sessionmaker() as session:
        row = ActivationToken(token="tok-update-test", status="pending", asaas_customer_id="cus_novo_tok")
        session.add(row)
        await session.commit()

    await token_service.consume_activation_token("tok-update-test", "888003001")

    async with db_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == "888003001"))

    assert user.asaas_customer_id == "cus_novo_tok"


# ── Fix #12: fallback OpenAI sem observabilidade ─────────────────────────────

@pytest.mark.asyncio
async def test_openai_api_error_returns_fallback_with_error_event(monkeypatch, sample_verse):
    """Erro operacional da API (quota, auth, timeout) → fallback + evento openai_api_error ERROR."""
    import logging
    import app.content_service as content_service

    captured = []

    def capture_event(logger, event, level=logging.INFO, **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(content_service, "log_event", capture_event)

    def bad_client():
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(content_service, "_get_openai_client", bad_client)

    result = await content_service.generate_explanation_content(sample_verse)

    assert result.is_fallback is True
    error_events = [e for e in captured if e["event"] == "openai_api_error"]
    assert len(error_events) == 1
    assert error_events[0]["level"] == logging.ERROR
    assert error_events[0]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_openai_parse_error_returns_fallback_with_warning_event(monkeypatch, sample_verse):
    """Resposta não-JSON da OpenAI → fallback + evento openai_parse_error WARNING."""
    import logging
    import app.content_service as content_service

    captured = []

    def capture_event(logger, event, level=logging.INFO, **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(content_service, "log_event", capture_event)

    class FakeMessage:
        content = "texto livre sem json valido nenhum"

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

    result = await content_service.generate_explanation_content(sample_verse)

    assert result.is_fallback is True
    parse_events = [e for e in captured if e["event"] == "openai_parse_error"]
    assert len(parse_events) == 1
    assert parse_events[0]["level"] == logging.WARNING


@pytest.mark.asyncio
async def test_openai_empty_response_returns_fallback_with_warning_event(monkeypatch, sample_verse):
    """Resposta vazia ou curtíssima da OpenAI → fallback + evento openai_empty_response WARNING."""
    import logging
    import app.content_service as content_service

    captured = []

    def capture_event(logger, event, level=logging.INFO, **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(content_service, "log_event", capture_event)

    class FakeMessage:
        content = "ok"  # < 20 chars após sanitização

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

    result = await content_service.generate_explanation_content(sample_verse)

    assert result.is_fallback is True
    empty_events = [e for e in captured if e["event"] == "openai_empty_response"]
    assert len(empty_events) == 1
    assert empty_events[0]["level"] == logging.WARNING


# ── Fix #9: record_user_interaction observável em WARNING ─────────────────────

@pytest.mark.asyncio
async def test_record_user_interaction_warns_on_db_failure(monkeypatch):
    """Falha de DB em record_user_interaction → evento WARNING, sem exceção propagada."""
    import logging
    import app.subscription_service as subscription_service

    captured = []

    def capture_event(logger, event, level=logging.INFO, **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(subscription_service, "log_event", capture_event)

    class _BrokenCtx:
        async def __aenter__(self):
            raise RuntimeError("DB connection pool exhausted")
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(subscription_service, "SessionLocal", lambda: _BrokenCtx())

    # Não deve propagar exceção — é fire-and-forget
    await subscription_service.record_user_interaction("u-fail-warn")

    warn_events = [e for e in captured if e["event"] == "record_interaction_failed"]
    assert len(warn_events) == 1
    assert warn_events[0]["level"] == logging.WARNING
    assert warn_events[0]["error_type"] == "RuntimeError"
    assert warn_events[0]["telegram_user_id"] == "u-fail-warn"


@pytest.mark.asyncio
async def test_record_user_interaction_succeeds_normally(db_sessionmaker):
    """Caminho normal: last_interaction_at é atualizado sem erro."""
    import app.subscription_service as subscription_service

    user = await subscription_service.get_or_create_user("u-interact-ok")
    original_ts = user.last_interaction_at

    await subscription_service.record_user_interaction("u-interact-ok")

    from sqlalchemy import select
    from app.models import User
    async with db_sessionmaker() as session:
        updated = await session.scalar(select(User).where(User.telegram_user_id == "u-interact-ok"))

    assert updated.last_interaction_at is not None
    if original_ts is not None:
        assert updated.last_interaction_at >= original_ts


# ── Fix #11: rate limiter persistente entre restarts ─────────────────────────

def test_rate_limiter_persists_after_connection_reset(tmp_path):
    """Estado do rate limiter sobrevive reset de conexão (simula restart do processo)."""
    from app.rate_limiter import check_rate_limit, _reset_connection

    db_path = tmp_path / "persist_test.db"
    _reset_connection(db_path)

    # 2 de 3 chamadas permitidas
    assert check_rate_limit("u:cmd", max_calls=3, window_seconds=3600) is True
    assert check_rate_limit("u:cmd", max_calls=3, window_seconds=3600) is True

    # Simula restart: fecha e reabre a mesma conexão
    _reset_connection(db_path)

    # Estado persiste: só mais 1 call permitido
    assert check_rate_limit("u:cmd", max_calls=3, window_seconds=3600) is True
    assert check_rate_limit("u:cmd", max_calls=3, window_seconds=3600) is False


def test_rate_limiter_reset_clears_state(tmp_path):
    """reset_rate_limit limpa apenas a chave especificada; outra chave não é afetada."""
    from app.rate_limiter import check_rate_limit, reset_rate_limit, _reset_connection

    _reset_connection(tmp_path / "reset_test.db")

    for _ in range(3):
        check_rate_limit("u:esgotado", max_calls=3, window_seconds=3600)

    assert check_rate_limit("u:esgotado", max_calls=3, window_seconds=3600) is False

    reset_rate_limit("u:esgotado")

    # Após reset, chave liberada
    assert check_rate_limit("u:esgotado", max_calls=3, window_seconds=3600) is True


# ── Fix #9 v2: retorno bool de record_user_interaction ───────────────────────

@pytest.mark.asyncio
async def test_record_user_interaction_returns_true_on_success(db_sessionmaker):
    """Caminho feliz retorna True — chamador sabe que o estado foi persistido."""
    import app.subscription_service as subscription_service

    await subscription_service.get_or_create_user("u-retval-ok")
    result = await subscription_service.record_user_interaction("u-retval-ok")

    assert result is True


@pytest.mark.asyncio
async def test_record_user_interaction_returns_false_on_db_failure(monkeypatch):
    """Falha de DB retorna False sem propagar exceção — chamador pode reagir se quiser."""
    import app.subscription_service as subscription_service

    class _BrokenCtx:
        async def __aenter__(self):
            raise RuntimeError("DB unavailable")
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(subscription_service, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(subscription_service, "SessionLocal", lambda: _BrokenCtx())

    result = await subscription_service.record_user_interaction("u-retval-fail")

    assert result is False


# ── Fix #11 v2: TTL, cleanup automático, write-path otimizado ────────────────

def test_rate_limiter_ttl_expired_entries_dont_count(tmp_path):
    """Entradas fora da janela de tempo não contam para o limite."""
    from app.rate_limiter import _reset_connection, _db, check_rate_limit

    _reset_connection(tmp_path / "ttl_test.db")
    db = _db()

    # Insere entrada antiga (2x fora da janela de 60s)
    old_ts = datetime.utcnow().timestamp() - 120
    with db:
        db.execute("INSERT INTO rate_limit_events (key, ts) VALUES (?,?)", ("u:cmd", old_ts))

    # Com max_calls=1, a entrada antiga não deve contar → primeira chamada permitida
    assert check_rate_limit("u:cmd", max_calls=1, window_seconds=60) is True


def test_rate_limiter_expired_entries_cleaned_up_after_check(tmp_path):
    """check_rate_limit remove entradas expiradas da chave verificada do banco."""
    from app.rate_limiter import _reset_connection, _db, check_rate_limit

    _reset_connection(tmp_path / "cleanup_test.db")
    db = _db()

    old_ts = datetime.utcnow().timestamp() - 120
    with db:
        db.execute("INSERT INTO rate_limit_events (key, ts) VALUES (?,?)", ("u:cmd", old_ts))

    (before,) = db.execute("SELECT COUNT(*) FROM rate_limit_events WHERE key=?", ("u:cmd",)).fetchone()
    assert before == 1

    check_rate_limit("u:cmd", max_calls=5, window_seconds=60)

    (after,) = db.execute("SELECT COUNT(*) FROM rate_limit_events WHERE key=?", ("u:cmd",)).fetchone()
    # Antiga removida; nova inserida → exatamente 1 entrada (a atual)
    assert after == 1


def test_rate_limiter_global_cleanup_purges_all_stale_keys(tmp_path):
    """Limpeza global remove entradas antigas de chaves inativas que nunca são re-consultadas."""
    from app.rate_limiter import _reset_connection, _db, check_rate_limit

    _reset_connection(tmp_path / "global_cleanup_test.db")
    db = _db()

    # Entrada antiga de key inativa (25h — acima do GLOBAL_CLEANUP_MAX_AGE de 24h)
    very_old_ts = datetime.utcnow().timestamp() - 90000
    with db:
        db.execute(
            "INSERT INTO rate_limit_events (key, ts) VALUES (?,?)",
            ("inactive:user", very_old_ts),
        )

    # Após _reset_connection, _last_global_cleanup=0 → primeiro check dispara cleanup global
    check_rate_limit("active:user", max_calls=10, window_seconds=3600)

    (count,) = db.execute(
        "SELECT COUNT(*) FROM rate_limit_events WHERE key=?", ("inactive:user",)
    ).fetchone()
    assert count == 0


def test_rate_limiter_blocked_requests_do_not_insert(tmp_path):
    """Chamadas bloqueadas usam o read-path: nenhuma escrita acontece no banco."""
    from app.rate_limiter import _reset_connection, _db, check_rate_limit

    _reset_connection(tmp_path / "blocked_test.db")
    db = _db()

    for _ in range(3):
        check_rate_limit("u:cmd", max_calls=3, window_seconds=3600)

    (count_at_limit,) = db.execute(
        "SELECT COUNT(*) FROM rate_limit_events WHERE key=?", ("u:cmd",)
    ).fetchone()

    # Chamada bloqueada — read-path apenas, sem INSERT
    result = check_rate_limit("u:cmd", max_calls=3, window_seconds=3600)
    assert result is False

    (count_after_blocked,) = db.execute(
        "SELECT COUNT(*) FROM rate_limit_events WHERE key=?", ("u:cmd",)
    ).fetchone()
    assert count_after_blocked == count_at_limit


# ── Fix áudio explicação: normalização TTS + contexto ────────────────────────

def test_normalize_for_tts_converts_biblical_references():
    """X:Y em texto bíblico vira 'capítulo X versículo Y' para leitura natural."""
    from app.content_service import _normalize_for_tts

    assert _normalize_for_tts("João 13:53") == "João capítulo 13 versículo 53"
    assert _normalize_for_tts("Salmos 23:1 e Romanos 8:28") == (
        "Salmos capítulo 23 versículo 1 e Romanos capítulo 8 versículo 28"
    )
    assert _normalize_for_tts("texto sem referência") == "texto sem referência"


def test_build_explanation_audio_text_includes_context(sample_verse):
    """Áudio da explicação deve conter Explicação, Contexto e Aplicação."""
    from app.content_service import ReflectionContent, build_explanation_audio_text

    reflection = ReflectionContent(
        explanation="Esta passagem revela o amor de Deus.",
        context="Escrito por Paulo durante a prisão.",
        application="Praticar confiança hoje.",
        prayer="Senhor, guia-me.",
        summary="s",
        is_fallback=False,
    )
    result = build_explanation_audio_text(sample_verse, reflection)

    assert "Esta passagem revela o amor de Deus." in result
    assert "Escrito por Paulo durante a prisão." in result
    assert "Praticar confiança hoje." in result
    assert "Contexto" in result
    assert "Aplicação" in result


def test_build_explanation_audio_text_normalizes_verse_reference(sample_verse):
    """Referência do versículo (ex: Salmos 23:1) é normalizada no áudio para TTS."""
    from app.content_service import ReflectionContent, build_explanation_audio_text

    reflection = ReflectionContent(
        explanation="Como em João 3:16, Deus amou.",
        context="Contexto histórico.",
        application="Aplicação prática.",
        prayer="p",
        summary="s",
        is_fallback=False,
    )
    result = build_explanation_audio_text(sample_verse, reflection)

    # sample_verse é Salmos 23:1 — referência deve estar normalizada
    assert "23:1" not in result
    assert "capítulo 23 versículo 1" in result
    # Referência inline na explicação também normalizada
    assert "3:16" not in result
    assert "capítulo 3 versículo 16" in result


# ── SessionStore ──────────────────────────────────────────────────────────────

class _FakeContext:
    def __init__(self):
        self.user_data = {}


def test_session_store_get_set():
    from app.session_state import SessionStore

    ctx = _FakeContext()
    store = SessionStore(ctx)

    assert store.get("key") is None
    store.set("key", "value")
    assert store.get("key") == "value"


def test_session_store_get_set_state():
    from app.session_state import JourneyState, SessionStore

    ctx = _FakeContext()
    store = SessionStore(ctx)

    assert store.get_state() is None

    store.set_state(JourneyState.VERSE)
    assert store.get_state() == JourneyState.VERSE

    store.set_state(JourneyState.EXPLANATION)
    assert store.get_state() == JourneyState.EXPLANATION

    store.set_state(JourneyState.REFLECTION)
    assert store.get_state() == JourneyState.REFLECTION

    store.set_state(JourneyState.PRAYER)
    assert store.get_state() == JourneyState.PRAYER


def test_session_store_invalid_state_returns_none():
    from app.session_state import SessionStore

    ctx = _FakeContext()
    ctx.user_data["_journey_state"] = "unknown_garbage"
    store = SessionStore(ctx)

    assert store.get_state() is None


def test_session_store_backed_by_user_data():
    """SessionStore lê e escreve diretamente no dict user_data."""
    from app.session_state import JourneyState, SessionStore

    ctx = _FakeContext()
    store = SessionStore(ctx)
    store.set_state(JourneyState.EXPLANATION)

    assert ctx.user_data["_journey_state"] == "explanation_done"


# ── Audio hash-based invalidation ─────────────────────────────────────────────

def test_audio_cache_hit_when_text_unchanged(tmp_path, monkeypatch):
    """Áudio existente com hash correto → cache hit, sem regerar."""
    import app.audio_service as audio_service
    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_path)

    verse = {"book": "Salmos", "chapter": "23", "verse": "1"}
    text = "Texto de teste para áudio."

    path = audio_service.build_named_audio_path("teste", verse)
    path.write_bytes(b"fake_mp3_data")
    audio_service._save_audio_hash(path, text)

    assert audio_service._audio_is_fresh(path, text) is True


def test_audio_cache_invalidated_when_text_changes(tmp_path, monkeypatch):
    """Áudio existente com hash diferente → não é fresh (será regenerado)."""
    import app.audio_service as audio_service
    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_path)

    verse = {"book": "Salmos", "chapter": "23", "verse": "1"}
    original_text = "Texto original."
    updated_text = "Texto atualizado após correção."

    path = audio_service.build_named_audio_path("teste", verse)
    path.write_bytes(b"fake_mp3_data")
    audio_service._save_audio_hash(path, original_text)

    assert audio_service._audio_is_fresh(path, updated_text) is False


def test_audio_not_fresh_when_hash_file_missing(tmp_path, monkeypatch):
    """Áudio sem arquivo .content_hash → não é fresh (gerado antes do versionamento)."""
    import app.audio_service as audio_service
    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_path)

    verse = {"book": "Salmos", "chapter": "23", "verse": "1"}
    path = audio_service.build_named_audio_path("teste", verse)
    path.write_bytes(b"fake_mp3_data")
    # nenhum arquivo .content_hash criado

    assert audio_service._audio_is_fresh(path, "qualquer texto") is False


def test_audio_not_fresh_when_mp3_missing(tmp_path, monkeypatch):
    """Sem arquivo .mp3 → não é fresh."""
    import app.audio_service as audio_service
    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_path)

    verse = {"book": "Salmos", "chapter": "23", "verse": "1"}
    path = audio_service.build_named_audio_path("teste", verse)
    # arquivo não existe

    assert audio_service._audio_is_fresh(path, "qualquer texto") is False
