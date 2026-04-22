import pytest
from sqlalchemy import select

from app.models import Subscription, User, VerseHistory


@pytest.mark.asyncio
async def test_start_returns_welcome_message(db_sessionmaker, fake_update, fake_context):
    import app.bot as bot_module

    await bot_module.start(fake_update, fake_context)

    assert len(fake_update.effective_message.replies) == 1
    assert "Seja bem-vindo" in fake_update.effective_message.replies[0]["text"]

    async with db_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(fake_update.effective_user.id)))
    assert user is not None


@pytest.mark.asyncio
async def test_start_with_token_activates_subscription(db_sessionmaker, fake_update):
    import app.bot as bot_module
    import app.token_service as token_service

    token = await token_service.create_activation_token()
    fake_context = type("Ctx", (), {"args": [token], "user_data": {}})()

    await bot_module.start(fake_update, fake_context)

    assert "ativado" in fake_update.effective_message.replies[0]["text"].lower()

    async with db_sessionmaker() as session:
        sub = await session.scalar(
            select(Subscription).join(User).where(User.telegram_user_id == str(fake_update.effective_user.id))
        )
    assert sub is not None
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_assinar_returns_subscription_message(fake_update, fake_context):
    import app.bot as bot_module

    await bot_module.assinar(fake_update, fake_context)

    assert len(fake_update.effective_message.replies) == 1
    assert "Ative seu acesso premium" in fake_update.effective_message.replies[0]["text"]


@pytest.mark.asyncio
async def test_versiculo_flow_sends_text_audio_and_history(
    db_sessionmaker,
    fake_update,
    fake_context,
    monkeypatch,
    sample_verse,
):
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module

    async def fake_send_verse_audio(message, verse):
        message.audios.append({"title": "audio-versiculo", "verse": verse})

    async def fake_get_random_verse_for_user(user_id):
        return sample_verse

    async def fake_user_has_active_subscription(user_id):
        return True

    async def fake_get_active_journey(session_factory, user_id):
        return None

    monkeypatch.setattr(bot_flows_module, "get_random_verse_for_user", fake_get_random_verse_for_user)
    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_flows_module, "send_verse_audio", fake_send_verse_audio)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)

    await bot_module.versiculo(fake_update, fake_context)

    assert "📖 Salmos 23:1" in fake_update.effective_message.replies[0]["text"]
    assert len(fake_update.effective_message.audios) == 1

    async with db_sessionmaker() as session:
        history = (
            await session.execute(
                select(VerseHistory).where(VerseHistory.telegram_user_id == str(fake_update.effective_user.id))
            )
        ).scalars().all()
    assert len(history) == 1


@pytest.mark.asyncio
async def test_explicar_flow_uses_last_verse_and_sends_reflection_audio(
    fake_update,
    fake_context,
    monkeypatch,
    sample_verse,
):
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module
    from tests.conftest import build_fake_reflection

    async def fake_send_reflection_audio(message, verse, reflection):
        message.audios.append({"title": "audio-explicacao", "verse": verse, "reflection": reflection})

    async def fake_user_has_active_subscription(user_id):
        return True

    async def fake_get_or_create_reflection_content(session_factory, user_id, verse, depth, journey_title):
        return build_fake_reflection(depth)

    async def fake_get_active_journey(session_factory, user_id):
        return None

    async def fake_get_user_explanation_depth(session_factory, user_id):
        return "balanced"

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_flows_module, "get_or_create_reflection_content", fake_get_or_create_reflection_content)
    monkeypatch.setattr(bot_flows_module, "send_reflection_audio", fake_send_reflection_audio)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    monkeypatch.setattr(bot_flows_module, "get_user_explanation_depth", fake_get_user_explanation_depth)
    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.explicar(fake_update, fake_context)

    assert any("Reflexão sobre Salmos 23:1" in item["text"] for item in fake_update.effective_message.replies)
    assert len(fake_update.effective_message.audios) == 1


@pytest.mark.asyncio
async def test_meuultimo_returns_cached_last_verse(fake_update, fake_context, sample_verse):
    import app.bot as bot_module

    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.meuultimo(fake_update, fake_context)

    assert len(fake_update.effective_message.replies) == 1
    assert "Salmos 23:1" in fake_update.effective_message.replies[0]["text"]


@pytest.mark.asyncio
async def test_explicar_returns_human_error_message_on_failure(fake_update, fake_context, monkeypatch, sample_verse):
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module

    async def raise_error(*args, **kwargs):
        raise RuntimeError("falha-openai")

    async def fake_get_user_explanation_depth(session_factory, user_id):
        return "balanced"

    async def fake_get_active_journey(session_factory, user_id):
        return None

    monkeypatch.setattr(bot_module, "user_has_active_subscription", lambda user_id: True)
    monkeypatch.setattr(bot_flows_module, "get_or_create_reflection_content", raise_error)
    monkeypatch.setattr(bot_flows_module, "get_user_explanation_depth", fake_get_user_explanation_depth)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.explicar(fake_update, fake_context)

    assert "não pôde ser preparada" in fake_update.effective_message.replies[0]["text"]


@pytest.mark.asyncio
async def test_versiculo_requires_active_subscription(fake_update, fake_context, monkeypatch):
    import app.bot as bot_module

    monkeypatch.setattr(bot_module, "user_has_active_subscription", lambda user_id: False)

    await bot_module.versiculo(fake_update, fake_context)

    assert any("premium" in r["text"].lower() or "assinar" in r["text"].lower() for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_versiculo_respects_rate_limit(fake_update, fake_context, monkeypatch, sample_verse):
    import app.bot as bot_module

    async def fake_send_verse_flow(update, context, verse=None):
        pass

    async def fake_user_has_active_subscription(user_id):
        return True

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_module, "send_verse_flow", fake_send_verse_flow)

    user_id = fake_update.effective_user.id
    from app.rate_limiter import reset_rate_limit
    reset_rate_limit(f"{user_id}:versiculo")

    from app.config import RATE_LIMIT_VERSICULO
    for _ in range(RATE_LIMIT_VERSICULO):
        await bot_module.versiculo(fake_update, fake_context)

    await bot_module.versiculo(fake_update, fake_context)

    last_reply = fake_update.effective_message.replies[-1]["text"]
    assert "muitas solicitações" in last_reply or "Aguarde" in last_reply


@pytest.mark.asyncio
async def test_orar_sends_prayer_from_cached_reflection(fake_update, fake_context, monkeypatch, sample_verse):
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module
    from tests.conftest import build_fake_reflection

    async def fake_user_has_active_subscription(user_id):
        return True

    async def fake_get_active_journey(session_factory, user_id):
        return None

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    fake_context.user_data["last_verse"] = sample_verse
    fake_context.user_data["last_reflection"] = build_fake_reflection().as_dict()

    await bot_module.orar(fake_update, fake_context)

    assert len(fake_update.effective_message.replies) == 1
    assert "Salmos 23:1" in fake_update.effective_message.replies[0]["text"]


@pytest.mark.asyncio
async def test_favoritar_saves_and_confirms_verse(db_sessionmaker, fake_update, fake_context, monkeypatch, sample_verse):
    import app.bot as bot_module

    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.favoritar(fake_update, fake_context)

    assert any("Salmos 23:1" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_favoritos_empty_returns_message(db_sessionmaker, fake_update, fake_context):
    import app.bot as bot_module

    await bot_module.favoritos(fake_update, fake_context)

    assert any("favoritos ainda estão vazios" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_buscar_without_keyword_returns_usage_hint(fake_update, fake_context, monkeypatch):
    import app.bot as bot_module

    monkeypatch.setattr(bot_module, "user_has_active_subscription", lambda user_id: True)
    fake_context.args = []

    await bot_module.buscar(fake_update, fake_context)

    assert any("/buscar" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_buscar_finds_verses(db_sessionmaker, fake_update, fake_context, monkeypatch):
    import app.bot as bot_module
    from app.models import Verse

    async with db_sessionmaker() as session:
        session.add(Verse(book="Salmos", chapter=23, verse=1, text="O Senhor e meu pastor", reference="Salmos 23:1"))
        await session.commit()

    async def fake_user_has_active_subscription(user_id):
        return True

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    fake_context.args = ["pastor"]

    await bot_module.buscar(fake_update, fake_context)

    assert any("pastor" in r["text"].lower() or "Salmos" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_admin_silently_ignores_non_admin(fake_update, fake_context, monkeypatch):
    import app.bot as bot_module

    monkeypatch.setattr(bot_module, "is_admin", lambda uid: False)

    await bot_module.admin(fake_update, fake_context)

    assert len(fake_update.effective_message.replies) == 0


@pytest.mark.asyncio
async def test_admin_status_returns_stats(db_sessionmaker, fake_update, fake_context, monkeypatch):
    import app.bot as bot_module

    monkeypatch.setattr(bot_module, "is_admin", lambda uid: True)
    fake_context.args = ["status"]

    await bot_module.admin(fake_update, fake_context)

    assert any("Usuários" in r["text"] or "Status" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_meuplano_shows_active_subscription(db_sessionmaker, fake_update, fake_context):
    import app.bot as bot_module
    import app.subscription_service as subscription_service

    user = await subscription_service.get_or_create_user(str(fake_update.effective_user.id))
    await subscription_service.activate_subscription_for_user(user_id=user.id)

    await bot_module.meuplano(fake_update, fake_context)

    assert any("Ativa" in r["text"] or "ativo" in r["text"].lower() for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_meuplano_shows_no_account_message(db_sessionmaker, fake_update, fake_context):
    import app.bot as bot_module

    await bot_module.meuplano(fake_update, fake_context)

    replies = fake_update.effective_message.replies
    assert len(replies) == 1
    text = replies[0]["text"].lower()
    assert "conta" in text or "plano" in text or "assinatura" in text or "acesso" in text


@pytest.mark.asyncio
async def test_reflexao_uses_deep_depth(fake_update, fake_context, monkeypatch, sample_verse):
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module
    from tests.conftest import build_fake_reflection

    depths_used = []

    async def fake_get_or_create_reflection_content(session_factory, user_id, verse, depth, journey_title):
        depths_used.append(depth)
        return build_fake_reflection(depth)

    async def fake_send_reflection_audio(message, verse, reflection):
        pass

    async def fake_get_active_journey(session_factory, user_id):
        return None

    async def fake_get_user_explanation_depth(session_factory, user_id):
        return "balanced"

    monkeypatch.setattr(bot_module, "user_has_active_subscription", lambda uid: True)
    monkeypatch.setattr(bot_flows_module, "get_or_create_reflection_content", fake_get_or_create_reflection_content)
    monkeypatch.setattr(bot_flows_module, "send_reflection_audio", fake_send_reflection_audio)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    monkeypatch.setattr(bot_flows_module, "get_user_explanation_depth", fake_get_user_explanation_depth)
    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.reflexao(fake_update, fake_context)

    assert depths_used == ["deep"]
    assert any("Reflexão sobre Salmos 23:1" in r["text"] for r in fake_update.effective_message.replies)


@pytest.mark.asyncio
async def test_reflexao_audio_uses_reflexao_prefix_not_explicacao(
    tmp_audio_dirs, fake_update, fake_context, monkeypatch, sample_verse
):
    """Cache de /reflexao (deep) e /explicar (balanced) devem ser arquivos separados."""
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module
    from tests.conftest import build_fake_reflection
    from app.audio_service import AudioAsset

    prefixes_used = []

    async def spy_ensure(prefix, verse, text):
        prefixes_used.append(prefix)
        path = tmp_audio_dirs / f"{prefix}_spy.mp3"
        path.write_bytes(b"fake")
        return AudioAsset(key=prefix, path=path, cache_hit=False)

    async def fake_get_or_create(sf, uid, verse, depth, journey_title):
        return build_fake_reflection(depth)

    async def fake_active_subscription(uid):
        return True

    async def fake_get_active_journey(sf, uid):
        return None

    async def fake_get_depth(sf, uid):
        return "balanced"

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_active_subscription)
    monkeypatch.setattr(bot_flows_module, "get_or_create_reflection_content", fake_get_or_create)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    monkeypatch.setattr(bot_flows_module, "get_user_explanation_depth", fake_get_depth)
    monkeypatch.setattr(bot_flows_module, "ensure_named_audio_asset", spy_ensure)
    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.reflexao(fake_update, fake_context)

    assert "reflexao" in prefixes_used, f"Esperado 'reflexao' mas recebido: {prefixes_used}"
    assert "explicacao" not in prefixes_used


@pytest.mark.asyncio
async def test_orar_sends_prayer_audio(
    tmp_audio_dirs, fake_update, fake_context, monkeypatch, sample_verse
):
    """/orar deve enviar texto E áudio da oração."""
    import app.bot as bot_module
    import app.bot_flows as bot_flows_module
    from app.content_service import ReflectionContent
    from app.audio_service import AudioAsset

    prefixes_used = []

    async def spy_ensure(prefix, verse, text):
        prefixes_used.append(prefix)
        path = tmp_audio_dirs / f"{prefix}_spy.mp3"
        path.write_bytes(b"fake")
        return AudioAsset(key=prefix, path=path, cache_hit=False)

    async def fake_active_subscription(uid):
        return True

    async def fake_get_active_journey(sf, uid):
        return None

    cached_reflection = ReflectionContent(
        explanation="Explicacao.", context="Contexto.", application="Aplicacao.",
        prayer="Oração de teste da oração.", summary="Resumo.",
    )
    fake_context.user_data["last_verse"] = sample_verse
    fake_context.user_data["last_reflection"] = cached_reflection.as_dict()

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_active_subscription)
    monkeypatch.setattr(bot_flows_module, "get_active_journey", fake_get_active_journey)
    monkeypatch.setattr(bot_flows_module, "ensure_named_audio_asset", spy_ensure)

    await bot_module.orar(fake_update, fake_context)

    replies = fake_update.effective_message.replies
    audios = fake_update.effective_message.audios
    assert any("Oração" in r["text"] for r in replies), f"Sem reply de oração: {replies}"
    assert "oracao" in prefixes_used, f"Esperado 'oracao' mas recebido: {prefixes_used}"
    assert len(audios) == 1
