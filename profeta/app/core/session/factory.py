"""Factory do backend de sessão — singleton por processo.

Lógica de seleção:
1. Se REDIS_URL estiver configurado → tenta RedisBackend
2. Se Redis falhar no startup → loga aviso e cai para MemoryBackend
3. Se REDIS_URL não estiver configurado → usa MemoryBackend (padrão)

O singleton é inicializado lazily no primeiro uso (ou explicitamente via
`init_session_backend()` no lifespan do FastAPI / startup do bot).
"""

import logging
import os
from typing import Optional

from app.core.session.backend import SessionBackend

logger = logging.getLogger(__name__)

_backend: Optional[SessionBackend] = None


def get_session_backend() -> SessionBackend:
    """Retorna o backend de sessão configurado para este processo.

    Cria um MemoryBackend na primeira chamada se `init_session_backend`
    ainda não foi chamado (safe default para testes e dev).
    """
    global _backend
    if _backend is None:
        from app.core.session.memory import MemoryBackend
        _backend = MemoryBackend()
    return _backend


async def init_session_backend() -> SessionBackend:
    """Inicializa o backend conforme REDIS_URL. Chamar uma vez no startup.

    Retorna o backend ativo para que o caller possa logá-lo ou guardá-lo.
    """
    global _backend
    redis_url = os.getenv("REDIS_URL", "").strip()

    if redis_url:
        try:
            from app.core.session.redis_backend import RedisBackend
            _backend = await RedisBackend.create(redis_url)
            logger.info("SessionBackend: Redis (%s)", redis_url)
            return _backend
        except Exception as exc:
            logger.warning(
                "RedisBackend indisponível (%s) — usando MemoryBackend: %s",
                redis_url,
                exc,
            )

    from app.core.session.memory import MemoryBackend
    _backend = MemoryBackend()
    logger.info("SessionBackend: Memory (in-process)")
    return _backend


def reset_session_backend(backend: Optional[SessionBackend] = None) -> None:
    """Reseta o backend para testes. Passa None para forçar re-inicialização."""
    global _backend
    _backend = backend
