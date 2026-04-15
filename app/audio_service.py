import hashlib
from pathlib import Path

import edge_tts

AUDIO_DIR = Path("data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def build_audio_filename(text: str) -> str:
    hash_id = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"{hash_id}.mp3"


def get_audio_path(text: str) -> Path:
    filename = build_audio_filename(text)
    return AUDIO_DIR / filename


async def generate_tts_audio(text: str) -> Path:
    """
    Gera áudio apenas se não existir (cache)
    """
    path = get_audio_path(text)

    if path.exists():
        return path

    # 🔥 MELHOR VOZ ESTÁVEL
    communicate = edge_tts.Communicate(
        text=text,
        voice="pt-BR-AntonioNeural",
        rate="-15%",  # desacelera fala (ESSENCIAL)
    )

    await communicate.save(str(path))

    return path
