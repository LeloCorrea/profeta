# Roadmap de Escala — Profeta SaaS

## Gatilhos de escala (quando agir, não antes)

| Usuários ativos | Ação recomendada |
|----------------|-----------------|
| 0–500 | Stack atual (SQLite, in-memory, 1 VPS) |
| 500–2.000 | PostgreSQL + Redis rate limiter |
| 2.000–10.000 | Workers async para job diário, Redis queues |
| 10.000+ | Read replicas, CDN para áudio, múltiplas VPS |

## Fila de envio diário (Workers)

**Problema atual:** job diário envia sequencialmente para cada usuário.
Com 5.000 usuários e 3s por envio = 4 horas. Inviável.

**Solução:**
1. Job gera mensagens e publica em fila Redis (Redis Streams)
2. N workers consomem a fila em paralelo (asyncio.gather com semáforo)
3. Resultado: 5.000 usuários em ~15 minutos com 10 workers

```python
# Esboço da implementação
async def main_with_workers(user_ids, bot, worker_count=10):
    semaphore = asyncio.Semaphore(worker_count)
    async def bounded_send(uid):
        async with semaphore:
            return await _send_verse_with_retry(uid, bot, logger)
    await asyncio.gather(*[bounded_send(uid) for uid in user_ids])
```

## Dashboard Admin (próxima feature prioritária)

Hoje: `/admin status` e `/admin usuarios` via Telegram.
Próximo: painel web FastAPI com:
- Métricas de envio diário
- Usuários ativos / inadimplentes
- Revenue MRR
- Logs de erros

Implementar em `app/admin_api.py` como router FastAPI separado.

## WhatsApp / Multicanal

A arquitetura de services é canal-agnóstica. Para adicionar WhatsApp:
- Criar `app/whatsapp_bot.py` (análogo a `bot.py`)
- Reusar todos os services sem mudança
- Criar flows em `app/whatsapp_flows.py` (análogo a `bot_flows.py`)

## CRM / Afiliados

- Adicionar tabela `Referral` (referrer_id, referred_id, commission_rate)
- Webhook Asaas inclui `externalReference` com código do afiliado
- `payment_service.py` processa e credita comissão

## App Próprio

Quando sair do Telegram:
- API REST em `app/api/` (FastAPI routers)
- Autenticação JWT
- Os services existentes são reutilizados sem mudança
- `bot.py` e `bot_flows.py` tornam-se apenas mais um cliente da API
