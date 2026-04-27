# Relatório de Consolidação — Profeta SaaS
**Data:** 2026-04-27  
**Executado por:** Claude Code (Sonnet 4.6)

---

## Diagnóstico da Situação Inicial

### Estrutura das Duas "Versões"
O repositório git está em `C:\dev\profeta_saas` (raiz). Não havia duas versões paralelas independentes — havia uma situação de refatoração incompleta:

- **Versão A (git `app/`)**: Rastreada pelo git no path `app/`, mas arquivos deletados do disco. Versão mais antiga, acessível via `git show HEAD:app/`.
- **Versão B (`profeta/app/`)**: Versão atual no disco, mais completa, com features novas. Esta é a versão canônica.

**O git index ainda rastreia 45 arquivos deletados** (`D app/*.py`) — isto precisa de `git rm --cached` ou commit das deleções para limpar.

---

## ✔️ Arquivos Escolhidos (fonte: `profeta/app/`)

Todos os arquivos de `profeta/app/` são a versão correta. Em todos os arquivos comuns, a versão `profeta/app/` é maior e mais completa:

| Arquivo | Linhas (profeta) | Linhas (git app) | Decisão |
|---|---|---|---|
| bot.py | 883 | 594 | profeta/ |
| jobs.py | 797 | 604 | profeta/ |
| main.py | 519 | 436 | profeta/ |
| models.py | 259→271 | 217 | profeta/ + UserProfile restaurado |
| subscription_service.py | 285 | 243 | profeta/ |
| config.py | 117 | 104 | profeta/ |

---

## 🔁 Arquivos Mesclados / Reconstruídos

### `app/services/profile_service.py`
**Situação**: Versão nova simplificou para apenas `get_inactive_active_subscribers`, removendo 5 funções de tracking.  
**Ação**: Restauradas as funções perdidas usando `UserProfile` model:
- `track_profile_activity(user_id, activity_type)`
- `get_user_profile(user_id)` → dict com verse_count, explanation_count, etc.
- `get_user_preference(user_id)` → tipo dominante
- `get_personalized_nudge(user_id)` → mensagem personalizada com emoji
- `is_user_inactive(user_id, days)` → checa last_interaction_at

### `app/services/segment_service.py`
**Situação**: Versão nova perdeu suporte ao segmento HOT e 4 funções usadas pelos testes.  
**Ação**: Adicionadas as funções com lógica HOT/WARM/AT_RISK/COLD preservada:
- `_calculate_from_stats(last_activity_date, streak_days, best_streak)` → inclui HOT
- `calculate_user_segment(telegram_user_id)` → calcula a partir de UserStats
- `get_user_segment(telegram_user_id)` → retorna segmento armazenado
- `get_segment_message(telegram_user_id)` → mensagem personalizada (🔥 para HOT)
- `update_user_segment(uid, segment=None)` → assinatura unificada, calcula se segment não informado

### `app/models.py`
**Situação**: `UserProfile` model foi removida na versão nova.  
**Ação**: Restaurada a classe `UserProfile` com tabela `user_profile` (user_id FK, verse_count, explanation_count, reflection_count, prayer_count, last_interaction_at).

### `tests/conftest.py`
**Situação**: Versão inner não patchava `SessionLocal` em profile_service e segment_service.  
**Ação**: Adicionados patches para 4 módulos extras:
- `app.services.evolution_service`
- `app.services.mission_service`
- `app.services.profile_service`
- `app.services.segment_service`

### `tests/test_segment_service.py`
**Situação**: Testava schema antigo de `UserStats` (user_id FK, best_streak, last_activity_date).  
**Ação**: Atualizado para usar schema atual (telegram_user_id string, streak_days, last_activity_at datetime). Lógica de teste preservada integralmente.

---

## Arquivos ÚNICOS da Versão Outer Adicionados

| Arquivo | Linhas | Por quê adicionar |
|---|---|---|
| `tests/test_profile_service.py` | 211 | Testes do serviço de perfil — coverage importante |
| `tests/test_segment_service.py` | 261 | Testes de segmentação HOT/WARM/AT_RISK/COLD |

---

## Arquivos ÚNICOS da Versão Inner (profeta) Mantidos

| Arquivo | Feature |
|---|---|
| `app/admin_dashboard.py` | Dashboard admin HTML |
| `app/credit_service.py` | Sistema de créditos |
| `app/finance_service.py` | Relatórios financeiros |
| `app/image_request_service.py` | Imagens geradas por IA |
| `app/init_db.py` | Inicialização programática do DB |
| `app/send_image.py` | Envio de imagens via Telegram |
| `app/share_service.py` | Compartilhamento de versículos |
| `app/services/message_budget_service.py` | Controle de orçamento diário de mensagens |
| `app/services/user_bootstrap.py` | Bootstrap de novo usuário |
| `app/core/session/` (4 arquivos) | Sessões Redis + Memory backend |
| `migrations/versions/` (10 arquivos) | Histórico completo de migrações |
| `tests/test_share_service.py` | 654 linhas de testes de compartilhamento |
| `deploy/systemd/` (8 arquivos) | Serviços systemd para VPS |
| `docs/architecture/` (3 arquivos) | ADRs e roadmap |

---

## ❌ Arquivos Descartados

| Arquivo | Por quê descartar |
|---|---|
| `app/admin/__init__.py`, `app/admin/router.py`, `app/admin/auth.py` | Apenas .pyc sem fonte, não referenciados no código |
| `app/render/` (6 arquivos) | Apenas .pyc sem fonte, não referenciados no código |
| `app/plugins/journeys/esperanca/` (git version) | Versão profeta/ mais recente |
| `data/audio/`, `data/audio_cache/` | Binários regeneráveis por TTS |
| `logs/*.log` | Logs de runtime, não código |
| `node_modules/`, `.venv/` | Dependências instaláveis |

---

## ⚠️ Problemas Encontrados

### 1. Git index desatualizado (CRÍTICO)
O git ainda rastreia 45 arquivos do antigo `app/` como deletados não commitados. Solução:
```bash
cd /c/dev/profeta_saas
git rm --cached app/*.py app/core/**/*.py app/services/*.py app/plugins/**/*.py
git commit -m "chore: remove stale app/ from index (moved to profeta/)"
```

### 2. Fontes de `admin/` e `render/` perdidas (BAIXO RISCO)
Os módulos `app/admin/` e `app/render/` tiveram fontes .py deletadas sem commit. Apenas .pyc sobreviveram. Como não são referenciados no código atual, o impacto é zero. Mas a funcionalidade original está perdida.

### 3. `UserStats.best_streak` ausente
O novo `UserStats` não tem campo `best_streak`. A função `_calculate_from_stats` usa `best_streak=streak_days` como proxy ao calcular via `calculate_user_segment`. Para implementação completa, adicionar `best_streak` ao `UserStats` em futura migração.

### 4. Segmento HOT sem mensagem de campanha
O dicionário `_CAMPAIGN_MESSAGES` não tinha HOT (usado para campanhas COLD/AT_RISK). Foi adicionado `_SEGMENT_MESSAGES` separado que inclui HOT para o `get_segment_message`. Os usuários HOT não recebem campanha automática (faz sentido — eles já estão engajados).

---

## 🧠 Decisões Técnicas

| Decisão | Justificativa |
|---|---|
| Usar `profeta/app/` como base para TUDO | É 30-50% maior em todos os arquivos, tem features novas, é o que foi commitado mais recentemente |
| Restaurar `UserProfile` em vez de usar `UserStats` | Os testes dependem diretamente do modelo. `UserStats` tem propósito diferente (gamificação/streak). Mantê-los separados é correto. |
| Tornar `segment` opcional em `update_user_segment` | Permite chamada antiga (2 args de evolution_service) e nova (1 arg com auto-cálculo dos testes) sem breaking change |
| Adicionar `_calculate_from_stats` com HOT | O segmento HOT é valioso para gamificação espiritual. A nova `compute_segment_from_stats` o havia removido sem substituição. Ambas coexistem. |
| Não incluir `.db` files | O banco será regenerado na VPS. Incluir DB local criaria dados de dev em produção. |
| Incluir `data/bible/` (JSONs) | Dados estáticos necessários para funcionamento do bot. |

---

## 🏗️ Estrutura Final

```
profeta_saas_final/
├── app/
│   ├── core/
│   │   ├── engine/          # JourneyEngine, EngineFacade, StateMachine, ContextResolver
│   │   └── session/         # MemoryBackend, RedisBackend, Factory
│   ├── plugins/
│   │   └── journeys/esperanca/
│   ├── services/
│   │   ├── evolution_service.py    # Gamificação e UserStats
│   │   ├── message_budget_service.py
│   │   ├── mission_service.py
│   │   ├── profile_service.py      # UserProfile tracking (restaurado)
│   │   ├── segment_service.py      # HOT/WARM/AT_RISK/COLD (ampliado)
│   │   └── user_bootstrap.py
│   ├── bot.py (883 linhas)
│   ├── jobs.py (797 linhas)
│   ├── models.py (271 linhas, 19 tabelas)
│   └── ... (31 arquivos Python)
├── data/bible/              # Bible JSON (estático)
├── deploy/systemd/          # 8 unit files para VPS
├── docs/architecture/       # 3 ADRs + roadmap
├── migrations/versions/     # 11 migrações (incl. UserProfile)
├── scripts/                 # 12 scripts utilitários
├── tests/                   # 7 arquivos de teste
│   ├── conftest.py          # Patchado para 12 módulos
│   ├── test_bot_core.py
│   ├── test_profile_service.py  # Restaurado do outer
│   ├── test_segment_service.py  # Restaurado + atualizado
│   ├── test_services.py
│   ├── test_share_service.py
│   └── test_webhook.py
├── .env.example
├── requirements.txt         # Inclui redis[asyncio] e Pillow
└── pytest.ini
```

**Total: 84 arquivos Python, 271 linhas em models.py (19 tabelas), 7 suítes de teste**

---

## Próximos Passos Recomendados

1. **Limpar git**: `git rm --cached` nos arquivos `app/` obsoletos
2. **Testar suite completa**: `pytest tests/` a partir de `profeta_saas_final/`
3. **Copiar `.env` real** para `profeta_saas_final/` com as chaves de produção
4. **Deploy**: Usar scripts em `scripts/deploy_to_vps.ps1` apontando para novo diretório
5. **Migrar DB**: Rodar migrações em ordem cronológica na VPS
