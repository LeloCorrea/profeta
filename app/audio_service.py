import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import edge_tts

from app.config import AUDIO_MIN_DISK_MB, CURRENT_TENANT, TTS_RATE, TTS_VOICE
from app.observability import get_logger, log_event
from app.tenant_config import TenantConfig

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


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".content_hash")


def _audio_is_fresh(path: Path, text: str) -> bool:
    """True if the audio file exists and was generated from this exact text."""
    if not path.exists():
        return False
    hp = _hash_path(path)
    if not hp.exists():
        return False
    return hp.read_text().strip() == _text_hash(text)


def _save_audio_hash(path: Path, text: str) -> None:
    _hash_path(path).write_text(_text_hash(text))


async def _save_tts_audio(path: Path, text: str, cfg: Optional[TenantConfig] = None) -> None:
    _cfg = cfg or CURRENT_TENANT
    communicate = edge_tts.Communicate(
        text=text,
        voice=_cfg.tts_voice or TTS_VOICE,
        rate=_cfg.tts_rate or TTS_RATE,
    )
    await communicate.save(str(path))


def audio_disk_space_ok() -> bool:
    """Returns False if free space on the audio volume is below AUDIO_MIN_DISK_MB."""
    try:
        free_mb = shutil.disk_usage(AUDIO_DIR).free / (1024 * 1024)
        return free_mb >= AUDIO_MIN_DISK_MB
    except OSError:
        return False


async def ensure_named_audio_asset(
    prefix: str,
    verse: dict[str, object],
    text: str,
    cfg: Optional[TenantConfig] = None,
) -> Optional[AudioAsset]:
    _cfg = cfg or CURRENT_TENANT
    path = build_named_audio_path(prefix, verse)
    key = path.stem

    if _audio_is_fresh(path, text):
        log_event(logger, "audio_cache_hit", cache_layer=prefix, audio_key=key)
        return AudioAsset(key=key, path=path, cache_hit=True)

    # Stale file (text changed) — delete before regenerating.
    if path.exists():
        path.unlink()
        hp = _hash_path(path)
        if hp.exists():
            hp.unlink()
        log_event(logger, "audio_cache_invalidated", cache_layer=prefix, audio_key=key)

    if not audio_disk_space_ok():
        log_event(
            logger,
            "audio_cache_space_guard",
            cache_layer=prefix,
            audio_key=key,
            min_mb=AUDIO_MIN_DISK_MB,
            level=30,
        )
        return None

    await _save_tts_audio(path, text, _cfg)
    _save_audio_hash(path, text)
    log_event(logger, "audio_generated", cache_layer=prefix, audio_key=key, voice=_cfg.tts_voice or TTS_VOICE)
    return AudioAsset(key=key, path=path, cache_hit=False)


def cleanup_old_audio_files(max_age_days: int = 7) -> int:
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    count = 0
    for path in AUDIO_DIR.glob("*.mp3"):
        mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            path.unlink()
            hp = _hash_path(path)
            if hp.exists():
                hp.unlink()
            log_event(logger, "audio_file_deleted", audio_key=path.stem, age_days=max_age_days)
            count += 1
    return count


async def generate_tts_audio(text: str, cfg: Optional[TenantConfig] = None) -> Path:
    _cfg = cfg or CURRENT_TENANT
    safe_name = normalize_text(text)[:80] or "audio"
    path = AUDIO_DIR / f"audio_{safe_name}.mp3"
    if not path.exists():
        await _save_tts_audio(path, text, _cfg)
        log_event(logger, "audio_generated", cache_layer="generic", audio_key=path.stem, voice=_cfg.tts_voice or TTS_VOICE)
    else:
        log_event(logger, "audio_cache_hit", cache_layer="generic", audio_key=path.stem)
    return path
