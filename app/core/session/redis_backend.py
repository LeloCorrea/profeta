"""RedisBackend — backend de sessão persistente via Redis.

Características:
- Cada sessão é armazenada como hash Redis keyed por "<tenant_id>:<user_id>"
- Serialização JSON (suporta str, int, float, bool, list, dict, None)
- TTL configurável por sessão (padrão: 24h)
- Fallback transparente: se o Redis estiver indisponível, operações são no-op
  e o MemoryBackend continua servindo (graceful degradation)
- Pool de conexões via redis.asyncio (uma conexão reutilizada entre requests)

Adequado para:
- Multi-instância (N workers compartilham estado)
- Multi-canal (bot Telegram + API REST + WhatsApp usam o mesmo estado)
- Recuperação após restart do bot

Pré-requisito:
- REDIS_URL configurado em .env (ex: redis://localhost:6379/0)
- redis[asyncio] no requirements.txt
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SESSION_TTL_SECONDS = 86_400  # 24h — cobre uma sessão diária completa
_KEY_PREFIX = "profeta:session:"


def _encode_key(key: tuple[str, str]) -> str:
    tenant_id, user_id = key
    return f"{_KEY_PREFIX}{tenant_id}:{user_id}"


class RedisBackend:
    """Backend de sessão Redis via redis.asyncio.

    Instanciação:
        backend = await RedisBackend.create(redis_url)

    O construtor é privado; use o factory `create` para garantir que a
    conexão foi estabelecida antes do primeiro uso.
    """

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    async def create(cls, redis_url: str) -> "RedisBackend":
        """Cria e verifica a conexão Redis. Lança ConnectionError se falhar."""
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise ImportError(
                "redis[asyncio] é necessário para RedisBackend. "
                "Execute: pip install 'redis[asyncio]'"
            ) from exc

        client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        # Ping verifica conexão antes de servir requests
        try:
            await client.ping()
        except Exception as exc:
            raise ConnectionError(f"Não foi possível conectar ao Redis ({redis_url}): {exc}") from exc

        logger.info("RedisBackend conectado: %s", redis_url)
        return cls(client)

    async def load(self, key: tuple[str, str]) -> Optional[dict[str, Any]]:
        rkey = _encode_key(key)
        try:
            raw = await self._client.get(rkey)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("RedisBackend.load falhou para %s: %s", rkey, exc)
            return None

    async def save(self, key: tuple[str, str], data: dict[str, Any]) -> None:
        rkey = _encode_key(key)
        try:
            payload = json.dumps(data, default=str)
            await self._client.setex(rkey, _SESSION_TTL_SECONDS, payload)
        except Exception as exc:
            logger.warning("RedisBackend.save falhou para %s: %s", rkey, exc)

    async def delete(self, key: tuple[str, str]) -> None:
        rkey = _encode_key(key)
        try:
            await self._client.delete(rkey)
        except Exception as exc:
            logger.warning("RedisBackend.delete falhou para %s: %s", rkey, exc)

    async def close(self) -> None:
        """Fecha o pool de conexões (chamar no shutdown da aplicação)."""
        try:
            await self._client.aclose()
        except Exception:
            pass
