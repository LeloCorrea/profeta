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
    context = bot_module.ContextTypes.DEFAULT_TYPE() if False else None
    del context
    fake_context = type("Ctx", (), {"args": [token], "user_data": {}})()

    await bot_module.start(fake_update, fake_context)

    assert "ativado" in fake_update.effective_message.replies[0]["text"].lower()

    async with db_sessionmaker() as session:
        sub = await session.scalar(select(Subscription).join(User).where(User.telegram_user_id == str(fake_update.effective_user.id)))
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

    async def fake_send_verse_audio(message, verse):
        message.audios.append({"title": "audio-versiculo", "verse": verse})

    async def fake_get_random_verse_for_user(user_id):
        return sample_verse

    async def fake_user_has_active_subscription(user_id):
        return True

    async def fake_get_active_journey(session_factory, user_id):
        return None

    monkeypatch.setattr(bot_module, "get_random_verse_for_user", fake_get_random_verse_for_user)
    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_module, "send_verse_audio", fake_send_verse_audio)
    monkeypatch.setattr(bot_module, "get_active_journey", fake_get_active_journey)

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
    from tests.conftest import build_fake_reflection

    async def fake_send_reflection_audio(message, verse, reflection):
        message.audios.append({"title": "audio-explicacao", "verse": verse, "reflection": reflection})

    async def fake_user_has_active_subscription(user_id):
        return True

    async def fake_generate_reflection_content(verse, depth, journey_title):
        return build_fake_reflection(depth)

    async def fake_get_active_journey(session_factory, user_id):
        return None

    async def fake_get_user_explanation_depth(session_factory, user_id):
        return "balanced"

    monkeypatch.setattr(bot_module, "user_has_active_subscription", fake_user_has_active_subscription)
    monkeypatch.setattr(bot_module, "generate_reflection_content", fake_generate_reflection_content)
    monkeypatch.setattr(bot_module, "send_reflection_audio", fake_send_reflection_audio)
    monkeypatch.setattr(bot_module, "get_active_journey", fake_get_active_journey)
    monkeypatch.setattr(bot_module, "get_user_explanation_depth", fake_get_user_explanation_depth)
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

    async def raise_error(*args, **kwargs):
        raise RuntimeError("falha-openai")

    monkeypatch.setattr(bot_module, "user_has_active_subscription", lambda user_id: True)
    monkeypatch.setattr(bot_module, "generate_reflection_content", raise_error)
    monkeypatch.setattr(bot_module, "get_user_explanation_depth", lambda session_factory, user_id: "balanced")
    monkeypatch.setattr(bot_module, "get_active_journey", lambda session_factory, user_id: None)
    fake_context.user_data["last_verse"] = sample_verse

    await bot_module.explicar(fake_update, fake_context)

    assert "não pôde ser preparada" in fake_update.effective_message.replies[0]["text"]
