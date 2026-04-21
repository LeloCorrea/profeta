# ADR-002: Migração Rate Limiter in-memory → Redis

**Status:** Planejado (não executar antes de múltiplas instâncias do bot)

## Contexto

O rate limiter atual (`app/rate_limiter.py`) é in-memory (dict Python).
Funciona perfeitamente para uma única instância do bot.

Problema ao escalar: múltiplas instâncias do bot não compartilham estado.
Um usuário poderia contornar o rate limit abrindo conexões em instâncias diferentes.

## Decisão

Manter in-memory enquanto houver uma única instância. Migrar para Redis quando:
- Múltiplas instâncias do bot em VPS diferentes
- Ou ao adicionar workers para processamento assíncrono

## Plano de execução

### 1. Adicionar Redis ao stack
```bash
pip install redis[asyncio]
```

```env
REDIS_URL=redis://localhost:6379/0
```

### 2. Substituição drop-in em `rate_limiter.py`
```python
import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None

async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL)
    return _redis

async def check_rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    r = await _get_redis()
    current = await r.incr(key)
    if current == 1:
        await r.expire(key, window_seconds)
    return current <= max_calls
```

A interface pública (`check_rate_limit`, `reset_rate_limit`) permanece idêntica.
Nenhuma mudança em `bot.py` ou handlers.

### 3. Fallback
Se Redis estiver indisponível, degradar graciosamente para permitir requisições
(não bloquear o usuário por falha de infraestrutura).

## Outros usos futuros do Redis

| Feature | Implementação |
|---------|--------------|
| Cache de sessão de usuário | `user:{id}:last_verse` com TTL |
| Fila de envio diário | Redis Streams ou Lista |
| Pub/Sub para webhook → bot | PUBLISH/SUBSCRIBE |
| Cache de explicações OpenAI | Alternativa ao DB para hot cache |

## Pontos de integração afetados

| Arquivo | Mudança |
|---------|---------|
| `app/rate_limiter.py` | Implementação interna (interface inalterada) |
| `app/config.py` | REDIS_URL |
| `requirements.txt` | redis[asyncio] |
| `deploy/systemd/*.service` | Sem mudança |
