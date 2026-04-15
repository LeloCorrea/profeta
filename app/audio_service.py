import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import edge_tts

from app.config import TTS_RATE, TTS_VOICE
from app.observability import get_logger, log_event

AUDIO_DIR = Path("data/audio")
AUDIO_CACHE_DIR = Path("data/audio_cache")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

logger = get_logger(__name__)


@dataclass
class AudioAsset:
    key: str
    path: Path
    cache_hit: bool
    telegram_file_id: Optional[str] = None


def build_audio_filename(text: str) -> str:
    hash_id = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"{hash_id}.mp3"


def sanitize_cache_segment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    return cleaned.strip("_") or "arquivo"


def build_named_cache_path(prefix: str, verse: dict[str, object]) -> Path:
    safe_book = sanitize_cache_segment(str(verse["book"]))
    chapter = str(verse["chapter"])
    verse_number = str(verse["verse"])
    return AUDIO_CACHE_DIR / f"{prefix}_{safe_book}_{chapter}_{verse_number}.mp3"


def get_audio_path(text: str) -> Path:
    filename = build_audio_filename(text)
    return AUDIO_DIR / filename


async def get_or_create_tts_audio(text: str) -> AudioAsset:
    path = get_audio_path(text)
    key = path.stem

    if path.exists():
        log_event(logger, "audio_cache_hit", cache_layer="tts", audio_key=key)
        return AudioAsset(key=key, path=path, cache_hit=True)

    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
        rate=TTS_RATE,
    )
    await communicate.save(str(path))
    log_event(logger, "audio_generated", cache_layer="tts", audio_key=key, voice=TTS_VOICE)
    return AudioAsset(key=key, path=path, cache_hit=False)


async def ensure_named_audio_asset(prefix: str, verse: dict[str, object], text: str) -> AudioAsset:
    cache_path = build_named_cache_path(prefix, verse)
    cache_key = cache_path.stem

    if cache_path.exists():
        log_event(logger, "audio_cache_hit", cache_layer=prefix, audio_key=cache_key)
        return AudioAsset(key=cache_key, path=cache_path, cache_hit=True)

    source_asset = await get_or_create_tts_audio(text)
    shutil.copy(source_asset.path, cache_path)
    log_event(
        logger,
        "audio_cache_miss",
        cache_layer=prefix,
        audio_key=cache_key,
        source_cache_hit=source_asset.cache_hit,
    )
    return AudioAsset(key=cache_key, path=cache_path, cache_hit=False)


async def generate_tts_audio(text: str) -> Path:
    asset = await get_or_create_tts_audio(text)
    return asset.path
