"""Protocolo abstrato para backends de sessão.

Qualquer backend deve implementar load/save/delete.
A chave é sempre uma tupla (tenant_id, user_id).

Interface assíncrona — MemoryBackend é trivialmente async;
RedisBackend usa awaits reais via redis.asyncio.
"""

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class SessionBackend(Protocol):
    """Contrato mínimo de persistência de sessão.

    load  : carrega o dict de sessão; retorna None se não existir
    save  : persiste o dict de sessão (write-through)
    delete: remove a sessão (logout / expiração)
    """

    async def load(self, key: tuple[str, str]) -> Optional[dict[str, Any]]:
        ...

    async def save(self, key: tuple[str, str], data: dict[str, Any]) -> None:
        ...

    async def delete(self, key: tuple[str, str]) -> None:
        ...
