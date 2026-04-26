# Roadmap de Escala — Profeta SaaS

## Prioridades Arquiteturais de Produto (pré-escala)

Estas duas evoluções devem ocorrer antes ou junto com a escala de usuários,
pois impactam diretamente a qualidade da experiência central do produto.

### A. Trilhas reais por versículo (trail_tags)

**Status:** Não implementado. Trilhas hoje são cosméticas (exibidas, não influenciam seleção).

**O que falta:**
1. Coluna `themes TEXT` (ou tabela `verse_themes`) no modelo `Verse`
2. Script de categorização: associar cada versículo a 1-3 temas (`fe`, `esperanca`, `perdao`, etc.)
3. Modificar `get_random_verse_for_user()` em `verse_service.py` para aceitar `journey_key` e aplicar lógica 80/20:
   - 80% versículos cujo tema bate com a trilha ativa
   - 20% versículos de outras trilhas (variedade saudável)
4. Migração de banco + reindexação

**Por que é prioritário:** A trilha ativa hoje não altera o que o usuário recebe. Isso reduz o valor percebido da feature de jornadas.

**Arquivos a modificar:**
- `app/models.py` — adicionar campo/tabela de temas
- `app/verse_service.py` — `get_random_verse_for_user` com `journey_key`
- `scripts/categorize_verses.py` — script de backfill de temas
- Migration Alembic correspondente

---

### B. Persistência de reflexão para /orar (eliminar fallback genérico)

**Status:** Gap identificado em auditoria de abril/2026.

**Problema atual:** `send_prayer_flow()` em `bot_flows.py` busca a oração de `context.user_data["last_reflection"]`
(memória de sessão). Se o bot reiniciar ou o usuário chegar em nova sessão sem ter rodado `/explicar`/`/reflexao`,
a oração cai para `build_default_prayer(verse)` — genérica, sem contexto da reflexão.

**Solução:**
1. Ao salvar `VerseExplanation` no banco, persistir também o campo `prayer` da reflexão
2. Em `send_prayer_flow()`, se `get_cached_reflection(context)` retornar `None`, buscar a última
   `VerseExplanation` do versículo no banco e usar seu campo `prayer` antes de ir para o fallback

**Arquivos a modificar:**
- `app/models.py` — adicionar coluna `prayer TEXT nullable` em `VerseExplanation`
- `app/content_service.py` — `get_or_create_reflection_content` persiste `prayer`
- `app/bot_flows.py` — `send_prayer_flow` tenta carregar prayer do banco antes do fallback
- Migration Alembic correspondente

---

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
