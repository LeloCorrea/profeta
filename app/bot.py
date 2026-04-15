import asyncio
import inspect
import json
import logging
import os
import random
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from app.audio_service import generate_tts_audio
from app.config import ASAAS_PAYMENT_LINK_URL, BOT_USERNAME, TELEGRAM_BOT_TOKEN
from app.db import SessionLocal
from app.models import Verse, VerseHistory
from app.subscription_service import (
    activate_subscription_for_user,
    get_or_create_user,
    user_has_active_subscription,
)
from app.token_service import consume_activation_token, validate_activation_token

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


logger = logging.getLogger(__name__)

BIBLE_PATH = Path("data/bible/bible.json")
RECENT_VERSE_BLOCK_SIZE = 30
DB_RANDOM_TRIES = 40
OPENAI_EXPLANATION_MODEL = os.getenv("OPENAI_EXPLANATION_MODEL", "gpt-5.4")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


# =============================
# GENERIC HELPERS
# =============================

async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    return cleaned.strip("_") or "arquivo"

def get_cache_audio_path(prefix: str, verse: dict[str, Any]) -> Path:
    safe_book = sanitize_filename(str(verse["book"]))
    chapter = str(verse["chapter"])
    verse_number = str(verse["verse"])

    filename = f"{prefix}_{safe_book}_{chapter}_{verse_number}.mp3"

    return Path("data/audio_cache") / filename

def normalize_verse(verse: Any) -> dict[str, Any]:
    if isinstance(verse, dict):
        return {
            "id": verse.get("id"),
            "book": str(verse.get("book", "")).strip(),
            "chapter": str(verse.get("chapter", "")).strip(),
            "verse": str(verse.get("verse", "")).strip(),
            "text": str(verse.get("text", "")).strip(),
        }

    return {
        "id": getattr(verse, "id", None),
        "book": str(getattr(verse, "book", "")).strip(),
        "chapter": str(getattr(verse, "chapter", "")).strip(),
        "verse": str(getattr(verse, "verse", "")).strip(),
        "text": str(getattr(verse, "text", "")).strip(),
    }


def verse_ref_tuple(verse: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(verse.get("book", "")).strip(),
        str(verse.get("chapter", "")).strip(),
        str(verse.get("verse", "")).strip(),
    )


def history_ref_tuple(item: Any) -> tuple[str, str, str]:
    return (
        str(getattr(item, "book", "")).strip(),
        str(getattr(item, "chapter", "")).strip(),
        str(getattr(item, "verse", "")).strip(),
    )


# =============================
# FALLBACK JSON (SEGURANÇA)
# =============================

@lru_cache(maxsize=1)
def load_verses() -> list[dict[str, Any]]:
    if not BIBLE_PATH.exists():
        return []

    with open(BIBLE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data if isinstance(data, list) else []


# =============================
# FORMATTERS
# =============================

def format_verse_reference(verse: dict[str, Any]) -> str:
    return f"{verse['book']} {verse['chapter']}:{verse['verse']}"


def format_verse_text(verse: dict[str, Any]) -> str:
    return f"📖 {format_verse_reference(verse)}\n\n“{verse['text']}”"


def build_tts_text(verse: dict[str, Any]) -> str:
    return (
        f"Versículo do dia. "
        f"{verse['book']}, capítulo {verse['chapter']}, versículo {verse['verse']}. "
        f"{verse['text']}"
    )


def build_explanation_tts_text(verse: dict[str, Any], explanation: str) -> str:
    return (
        f"Explicação do versículo. "
        f"{verse['book']}, capítulo {verse['chapter']}, versículo {verse['verse']}. "
        f"{explanation}"
    )


# =============================
# DB VERSE SERVICE
# =============================

async def get_random_verse_from_db(
    excluded_refs: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any] | None:
    excluded_refs = excluded_refs or set()

    async with SessionLocal() as session:
        total_stmt = select(func.count()).select_from(Verse)
        total = (await session.execute(total_stmt)).scalar_one_or_none() or 0

        if total <= 0:
            return None

        for _ in range(min(DB_RANDOM_TRIES, total)):
            offset = random.randint(0, total - 1)
            stmt = select(Verse).offset(offset).limit(1)
            verse_obj = (await session.execute(stmt)).scalar_one_or_none()

            if not verse_obj:
                continue

            verse = normalize_verse(verse_obj)
            if verse_ref_tuple(verse) not in excluded_refs:
                return verse

        stmt = select(Verse).limit(min(total, 500))
        verses = [normalize_verse(v) for v in (await session.execute(stmt)).scalars().all()]
        filtered = [v for v in verses if verse_ref_tuple(v) not in excluded_refs]

        if filtered:
            return random.choice(filtered)

        if verses:
            return random.choice(verses)

    return None


def get_random_verse_from_json(
    excluded_refs: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any] | None:
    excluded_refs = excluded_refs or set()
    verses = [normalize_verse(v) for v in load_verses()]

    if not verses:
        return None

    filtered = [v for v in verses if verse_ref_tuple(v) not in excluded_refs]
    return random.choice(filtered or verses)


# =============================
# HISTORY
# =============================

async def save_verse_history(update: Update, verse: dict[str, Any]) -> None:
    user = update.effective_user
    if not user:
        return

    async with SessionLocal() as session:
        session.add(
            VerseHistory(
                telegram_user_id=str(user.id),
                book=str(verse["book"]),
                chapter=str(verse["chapter"]),
                verse=str(verse["verse"]),
                text=str(verse["text"]),
            )
        )
        await session.commit()


async def get_last_verse_for_user(update: Update) -> dict[str, Any] | None:
    user = update.effective_user
    if not user:
        return None

    async with SessionLocal() as session:
        stmt = (
            select(VerseHistory)
            .where(VerseHistory.telegram_user_id == str(user.id))
            .order_by(VerseHistory.id.desc())
            .limit(1)
        )
        item = (await session.execute(stmt)).scalar_one_or_none()

    return normalize_verse(item) if item else None


async def get_recent_verse_refs_for_user(
    update: Update,
    limit: int = RECENT_VERSE_BLOCK_SIZE,
) -> set[tuple[str, str, str]]:
    user = update.effective_user
    if not user:
        return set()

    async with SessionLocal() as session:
        stmt = (
            select(VerseHistory)
            .where(VerseHistory.telegram_user_id == str(user.id))
            .order_by(VerseHistory.id.desc())
            .limit(limit)
        )
        items = (await session.execute(stmt)).scalars().all()

    return {history_ref_tuple(item) for item in items}


# =============================
# USER / SUBSCRIPTION
# =============================

async def ensure_user_record(update: Update) -> None:
    user = update.effective_user
    if not user:
        return

    try:
        await maybe_await(
            get_or_create_user(
                telegram_user_id=str(user.id),
                telegram_username=user.username,
                full_name=user.full_name,
            )
        )
        return
    except TypeError:
        pass
    except Exception:
        logger.exception("Falha ao criar/obter usuário com assinatura completa.")

    for args in (
        (str(user.id), user.username, user.full_name),
        (str(user.id), user.username),
        (str(user.id),),
    ):
        try:
            await maybe_await(get_or_create_user(*args))
            return
        except TypeError:
            continue
        except Exception:
            logger.exception("Falha ao criar/obter usuário.")
            return


async def require_active_subscription(update: Update) -> bool:
    user = update.effective_user
    message = update.message

    if not user or not message:
        return False

    await ensure_user_record(update)

    try:
        if await maybe_await(user_has_active_subscription(str(user.id))):
            return True
    except Exception:
        logger.exception("Erro ao validar assinatura ativa do usuário %s.", user.id)

    await message.reply_text(
        "⚠️ Seu acesso não está ativo.\n\n💳 Use /assinar para ativar."
    )
    return False


async def activate_user_from_token(user_id: str, token: str) -> None:
    validated = await maybe_await(validate_activation_token(token))
    if not validated:
        raise ValueError("Token inválido ou expirado.")

    consumed = False

    for call in (
        lambda: consume_activation_token(token, user_id),
        lambda: consume_activation_token(token=token, telegram_user_id=user_id),
        lambda: consume_activation_token(token),
    ):
        try:
            result = await maybe_await(call())
            consumed = result is not False
            break
        except TypeError:
            continue

    if not consumed:
        raise ValueError("Não foi possível consumir o token de ativação.")

    for call in (
        lambda: activate_subscription_for_user(user_id),
        lambda: activate_subscription_for_user(telegram_user_id=user_id),
        lambda: activate_subscription_for_user(user_id=user_id),
    ):
        try:
            await maybe_await(call())
            return
        except TypeError:
            continue

    raise RuntimeError("Não foi possível ativar a assinatura do usuário.")


# =============================
# CORE
# =============================

async def get_random_verse_for_user(update: Update) -> dict[str, Any] | None:
    recent_refs = await get_recent_verse_refs_for_user(update)

    verse = await get_random_verse_from_db(recent_refs)
    if verse:
        return verse

    return get_random_verse_from_json(recent_refs)


# =============================
# AUDIO SEND HELPERS
# =============================

async def send_audio_file(
    message,
    audio_path: str | Path,
    *,
    filename: str,
    title: str,
    performer: str,
    caption: str | None = None,
) -> None:
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Áudio não encontrado: {path}")

    with path.open("rb") as fh:
        telegram_file = InputFile(fh, filename=filename)
        await message.reply_audio(
            audio=telegram_file,
            title=title,
            performer=performer,
            caption=caption,
        )


# =============================
# VERSE SEND
# =============================

async def send_verse_text(update: Update, verse: dict[str, Any]) -> None:
    message = update.message
    if not message:
        return

    await message.reply_text(format_verse_text(verse))


async def send_verse_audio(update: Update, verse: dict[str, Any]) -> None:
    message = update.message
    if not message:
        return

    cache_path = get_cache_audio_path("versiculo", verse)

    # 🔁 CACHE HIT
    if cache_path.exists():
        with cache_path.open("rb") as f:
            await message.reply_audio(
                audio=InputFile(f, filename=cache_path.name),
                title=f"Áudio de {verse['book']} {verse['chapter']}:{verse['verse']}",
                performer="Profeta",
            )
        return

    # 🧠 CACHE MISS → gera
    tts_text = build_tts_text(verse)

    audio_path = await generate_tts_audio(tts_text)

    if not audio_path or not Path(audio_path).exists():
        raise FileNotFoundError(f"Áudio não encontrado: {audio_path}")

    # 💾 salva no cache
    import shutil
    shutil.copy(audio_path, cache_path)

    # 📤 envia
    with cache_path.open("rb") as f:
        await message.reply_audio(
            audio=InputFile(f, filename=cache_path.name),
            title=f"Áudio de {verse['book']} {verse['chapter']}:{verse['verse']}",
            performer="Profeta",
        )

async def send_verse_text_and_audio(update: Update, verse: dict[str, Any]) -> None:
    await save_verse_history(update, verse)
    await send_verse_text(update, verse)
    await send_verse_audio(update, verse)


# =============================
# AI EXPLAIN
# =============================

def extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text).strip()

    output = getattr(response, "output", None) or []
    parts: list[str] = []

    for item in output:
        content = getattr(item, "content", None) or []
        for block in content:
            value = getattr(block, "text", None)
            if value:
                parts.append(str(value))

    return "\n".join(parts).strip()


async def generate_explanation_text(verse: dict[str, Any]) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Configure OPENAI_API_KEY.")
    if OpenAI is None:
        raise RuntimeError("Pacote openai não está disponível.")

    prompt = (
        "Explique o versículo abaixo de forma simples, fiel ao texto bíblico, "
        "em português do Brasil, em no máximo 8 linhas. "
        "Evite inventar contexto não explícito. "
        f"Versículo: {verse['book']} {verse['chapter']}:{verse['verse']} - {verse['text']}"
    )

    def _run() -> str:
        client = OpenAI()
        response = client.responses.create(
            model=OPENAI_EXPLANATION_MODEL,
            input=prompt,
        )
        return extract_response_text(response)

    text = await asyncio.to_thread(_run)
    if not text:
        raise RuntimeError("A IA não retornou texto de explicação.")

    return text



async def send_explanation_audio(
    update: Update,
    verse: dict[str, Any],
    explanation: str,
) -> None:
    message = update.message
    if not message:
        return

    cache_path = get_cache_audio_path("explicacao", verse)

    # 🔁 CACHE HIT
    if cache_path.exists():
        with cache_path.open("rb") as f:
            await message.reply_audio(
                audio=InputFile(f, filename=cache_path.name),
                title=f"Explicação de {verse['book']} {verse['chapter']}:{verse['verse']}",
                performer="Profeta",
            )
        return

    # 🧠 CACHE MISS
    audio_text = build_explanation_tts_text(verse, explanation)

    audio_path = await generate_tts_audio(audio_text)

    if not audio_path or not Path(audio_path).exists():
        raise FileNotFoundError(f"Áudio não encontrado: {audio_path}")

    # 💾 salva no cache
    import shutil
    shutil.copy(audio_path, cache_path)

    # 📤 envia
    with cache_path.open("rb") as f:
        await message.reply_audio(
            audio=InputFile(f, filename=cache_path.name),
            title=f"Explicação de {verse['book']} {verse['chapter']}:{verse['verse']}",
            performer="Profeta",
        )

# =============================
# COMMANDS
# =============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user

    if not message or not user:
        return

    await ensure_user_record(update)

    token = context.args[0].strip() if context.args else None

    if token:
        try:
            await activate_user_from_token(str(user.id), token)
            await message.reply_text(
                "✅ Seu acesso foi ativado com sucesso.\n\n"
                "Agora você já pode usar /versiculo e /explicar."
            )
            return
        except Exception as exc:
            logger.exception("Falha na ativação por token.")
            await message.reply_text(
                f"⚠️ Não foi possível ativar seu acesso agora.\n\n{exc}"
            )
            return

    await message.reply_text(
        "🙏 Bem-vindo ao Profeta.\n\n"
        "Use /versiculo para receber um versículo com áudio.\n"
        "Use /explicar para receber a explicação do último versículo com áudio.\n"
        "Use /assinar para ativar seu acesso."
    )


async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    await message.reply_text(
        "Comandos disponíveis:\n\n"
        "/start - iniciar o bot\n"
        "/versiculo - receber um versículo com áudio\n"
        "/explicar - receber a explicação do último versículo com áudio\n"
        "/meuultimo - ver o último versículo enviado\n"
        "/assinar - ativar ou renovar seu acesso\n"
        "/ajuda - mostrar esta mensagem"
    )


async def assinar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    await message.reply_text(
        f"💳 Ative seu acesso aqui:\n{ASAAS_PAYMENT_LINK_URL}"
    )


async def meuultimo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    last = await get_last_verse_for_user(update)
    if not last:
        await message.reply_text(
            "Ainda não encontrei versículo no seu histórico. Use /versiculo primeiro."
        )
        return

    await message.reply_text(format_verse_text(last))


async def versiculo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    if not await require_active_subscription(update):
        return

    await message.chat.send_action(ChatAction.TYPING)

    verse = await get_random_verse_for_user(update)
    if not verse:
        await message.reply_text("Erro ao carregar versículo.")
        return

    try:
        await send_verse_text_and_audio(update, verse)
    except TelegramError:
        logger.exception("Falha ao enviar versículo para o usuário.")
        await message.reply_text(
            "⚠️ Não consegui enviar o versículo agora. Tente novamente."
        )
    except Exception:
        logger.exception("Falha geral ao montar/enviar versículo.")
        await message.reply_text(
            "⚠️ Ocorreu um erro ao gerar o versículo com áudio."
        )


async def explicar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    if not await require_active_subscription(update):
        return

    last = await get_last_verse_for_user(update)
    if not last:
        await message.reply_text("Use /versiculo primeiro.")
        return

    await message.chat.send_action(ChatAction.TYPING)

    try:
        explanation = await generate_explanation_text(last)
    except Exception as exc:
        logger.exception("Falha ao gerar explicação com IA.")
        await message.reply_text(
            f"⚠️ Não consegui gerar a explicação agora.\n\n{exc}"
        )
        return

    await message.reply_text(
        "📖 Explicação\n\n"
        f"{explanation}\n\n"
        "🙏 Posso aprofundar ou orar com você."
    )

    try:
        await send_explanation_audio(update, last, explanation)
    except TelegramError:
        logger.exception("Falha ao enviar áudio da explicação.")
        await message.reply_text(
            "⚠️ A explicação em texto foi enviada, mas o áudio falhou desta vez."
        )
    except Exception:
        logger.exception("Falha geral ao gerar/enviar áudio da explicação.")
        await message.reply_text(
            "⚠️ A explicação em texto foi enviada, mas não consegui gerar o áudio."
        )


# =============================
# ERROR HANDLER
# =============================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error

    if isinstance(error, Conflict):
        logger.error("Conflito do Telegram: existe outra instância do bot rodando.")
        return

    logger.exception("Erro não tratado no bot.", exc_info=error)


# =============================
# MAIN
# =============================

def main() -> None:
    setup_logging()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(CommandHandler("versiculo", versiculo))
    app.add_handler(CommandHandler("explicar", explicar))
    app.add_handler(CommandHandler("meuultimo", meuultimo))
    app.add_handler(CommandHandler("assinar", assinar))
    app.add_error_handler(on_error)

    print(f"Bot @{BOT_USERNAME} iniciado...")

    try:
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error(
            "Não foi possível iniciar: já existe outra instância consumindo updates deste bot."
        )
    except TelegramError:
        logger.exception("Falha do Telegram ao iniciar o bot.")


if __name__ == "__main__":
    main()
