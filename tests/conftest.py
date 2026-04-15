from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base


@pytest_asyncio.fixture
async def db_sessionmaker(tmp_path, monkeypatch):
    import app.bot as bot_module
    import app.db as db_module
    import app.main as main_module
    import app.payment_service as payment_service
    import app.subscription_service as subscription_service
    import app.token_service as token_service
    import app.verse_service as verse_service

    db_path = tmp_path / "profeta_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker)
    monkeypatch.setattr(bot_module, "SessionLocal", sessionmaker)
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(payment_service, "SessionLocal", sessionmaker)
    monkeypatch.setattr(subscription_service, "SessionLocal", sessionmaker)
    monkeypatch.setattr(token_service, "SessionLocal", sessionmaker)
    monkeypatch.setattr(verse_service, "SessionLocal", sessionmaker)

    yield sessionmaker

    await engine.dispose()


@pytest.fixture
def sample_verse():
    return {
        "id": 1,
        "book": "Salmos",
        "chapter": "23",
        "verse": "1",
        "text": "O Senhor e meu pastor e nada me faltara.",
    }


@pytest.fixture
def another_verse():
    return {
        "id": 2,
        "book": "Isaias",
        "chapter": "41",
        "verse": "10",
        "text": "Nao temas, porque eu sou contigo.",
    }


class FakeChat:
    async def send_action(self, action):
        return None


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.audios = []
        self.chat = FakeChat()

    async def reply_text(self, text, reply_markup=None):
        self.replies.append({"text": text, "reply_markup": reply_markup})

    async def reply_audio(self, audio=None, title=None, performer=None, caption=None):
        self.audios.append(
            {
                "audio": audio,
                "title": title,
                "performer": performer,
                "caption": caption,
            }
        )


class FakeUser:
    def __init__(self, user_id=123456, username="qa_user", full_name="QA User"):
        self.id = user_id
        self.username = username
        self.full_name = full_name


class FakeUpdate:
    def __init__(self, user_id=123456):
        self.effective_user = FakeUser(user_id=user_id)
        self.effective_message = FakeMessage()
        self.callback_query = None


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.user_data = {}


@pytest.fixture
def fake_update():
    return FakeUpdate()


@pytest.fixture
def fake_context():
    return FakeContext()


@pytest.fixture
def tmp_audio_dirs(tmp_path, monkeypatch):
    import app.audio_service as audio_service

    audio_dir = tmp_path / "audio"
    cache_dir = tmp_path / "audio_cache"
    audio_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(audio_service, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(audio_service, "AUDIO_CACHE_DIR", cache_dir)
    return audio_dir, cache_dir


def build_fake_reflection(depth="balanced"):
    from app.content_service import ReflectionContent

    return ReflectionContent(
        explanation="Explicacao de teste.",
        context="Contexto de teste.",
        application="Aplicacao de teste.",
        prayer="Oracao de teste.",
        summary="Resumo de teste.",
        depth=depth,
    )
