import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import edge_tts

from app.config import TTS_RATE, TTS_VOICE
from app.observability import get_logger, log_event

AUDIO_DIR = Path("data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

logger = get_logger(__name__)


@dataclass
class AudioAsset:
    key: str
    path: Path
    cache_hit: bool
    telegram_file_id: Optional[str] = None


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def build_audio_filename(prefix: str, verse: dict[str, object]) -> str:
    normalized_book = normalize_text(str(verse["book"])) or "livro"
    chapter = str(verse["chapter"]).strip()
    verse_number = str(verse["verse"]).strip()
    return f"{prefix}_{normalized_book}_{chapter}_{verse_number}.mp3"


def build_named_audio_path(prefix: str, verse: dict[str, object]) -> Path:
    return AUDIO_DIR / build_audio_filename(prefix, verse)


async def _save_tts_audio(path: Path, text: str) -> None:
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
        rate=TTS_RATE,
    )
    await communicate.save(str(path))


async def ensure_named_audio_asset(prefix: str, verse: dict[str, object], text: str) -> AudioAsset:
    path = build_named_audio_path(prefix, verse)
    key = path.stem

    if path.exists():
        log_event(logger, "audio_cache_hit", cache_layer=prefix, audio_key=key)
        return AudioAsset(key=key, path=path, cache_hit=True)

    await _save_tts_audio(path, text)
    log_event(logger, "audio_generated", cache_layer=prefix, audio_key=key, voice=TTS_VOICE)
    return AudioAsset(key=key, path=path, cache_hit=False)


def cleanup_old_audio_files(max_age_days: int = 7) -> int:
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    count = 0
    for path in AUDIO_DIR.glob("*.mp3"):
        mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            path.unlink()
            log_event(logger, "audio_file_deleted", audio_key=path.stem, age_days=max_age_days)
            count += 1
    return count


async def generate_tts_audio(text: str) -> Path:
    safe_name = normalize_text(text)[:80] or "audio"
    path = AUDIO_DIR / f"audio_{safe_name}.mp3"
    if not path.exists():
        await _save_tts_audio(path, text)
        log_event(logger, "audio_generated", cache_layer="generic", audio_key=path.stem, voice=TTS_VOICE)
    else:
        log_event(logger, "audio_cache_hit", cache_layer="generic", audio_key=path.stem)
    return path
