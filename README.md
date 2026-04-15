# Profeta

Backend do Profeta, um bot premium no Telegram com versículos, reflexão por IA, áudio, favoritos, jornadas espirituais e ativação por pagamento.

## Entradas principais

- API FastAPI: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Bot Telegram: `python -m app.bot`
- Job diário: `python -m app.jobs`

## Requisitos

- Python 3.9+
- Dependências de [requirements.txt](requirements.txt)

## Setup local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

No Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Variáveis importantes

- `TELEGRAM_BOT_TOKEN`
- `BOT_USERNAME`
- `DATABASE_URL`
- `PUBLIC_BASE_URL`
- `ASAAS_WEBHOOK_TOKEN`
- `ASAAS_PAYMENT_LINK_ID`
- `ASAAS_PAYMENT_LINK_URL`
- `OPENAI_API_KEY`
- `LOG_LEVEL`

## Testes

Comando padrão:

```bash
pytest -q
```

## Healthcheck

- `GET /health`
- `GET /`

## Estrutura relevante

- [app/bot.py](app/bot.py): handlers do bot e fluxos conversacionais
- [app/main.py](app/main.py): API, webhook e healthcheck
- [app/verse_service.py](app/verse_service.py): seleção de versículos e histórico
- [app/audio_service.py](app/audio_service.py): TTS e cache local de áudio
- [app/content_service.py](app/content_service.py): reflexão premium por IA
- [app/payment_service.py](app/payment_service.py): idempotência de pagamento e geração de token

## Operação

Documentação operacional e deploy em [DEPLOY.md](DEPLOY.md).
