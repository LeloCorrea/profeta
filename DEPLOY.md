# Deploy e Operação

## Objetivo

Colocar o Profeta em produção com bot, API e job diário rodando de forma previsível, com restart automático, logs úteis e troubleshooting básico.

## Ambiente recomendado

- Ubuntu 22.04+ ou outra VPS Linux com systemd
- Python 3.9 ou superior
- Nginx opcional na frente da API
- TLS gerenciado fora da aplicação

## Setup inicial

```bash
sudo apt update
sudo apt install -y python3.9 python3.9-venv git
git clone <repo-url> /opt/profeta/current
cd /opt/profeta/current
python3.9 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## Variáveis obrigatórias para produção

- `ENV=prod`
- `TELEGRAM_BOT_TOKEN`
- `BOT_USERNAME`
- `DATABASE_URL`
- `PUBLIC_BASE_URL`
- `ASAAS_WEBHOOK_TOKEN`

## Subir manualmente

API:

```bash
./scripts/start_api.sh
```

Bot:

```bash
./scripts/start_bot.sh
```

Job diário:

```bash
source .venv/bin/activate
python -m app.jobs
```

## Healthcheck

```bash
curl http://127.0.0.1:8000/health
```

Resposta esperada:

```json
{"status":"healthy","app":"profeta","env":"prod"}
```

## systemd

Exemplos em:

- [deploy/systemd/profeta-api.service](deploy/systemd/profeta-api.service)
- [deploy/systemd/profeta-bot.service](deploy/systemd/profeta-bot.service)

Instalação típica:

```bash
sudo cp deploy/systemd/profeta-api.service /etc/systemd/system/
sudo cp deploy/systemd/profeta-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now profeta-api
sudo systemctl enable --now profeta-bot
```

## Restart automático

Os serviços de exemplo usam:

- `Restart=always`
- `RestartSec=5`
- `WorkingDirectory` fixo
- `EnvironmentFile` com `.env`

## Logs e troubleshooting

API:

```bash
journalctl -u profeta-api -f
```

Bot:

```bash
journalctl -u profeta-bot -f
```

Checagens rápidas:

1. `systemctl status profeta-api`
2. `systemctl status profeta-bot`
3. validar `GET /health`
4. confirmar variáveis críticas no `.env`
5. rodar `pytest -q` antes de promover nova versão

## Procedimento seguro de atualização

```bash
cd /opt/profeta/current
git pull --ff-only
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
sudo systemctl restart profeta-api
sudo systemctl restart profeta-bot
```

## Riscos operacionais conhecidos

- SQLite é suficiente para operação inicial, mas concorrência alta pode exigir migração futura para Postgres.
- OpenAI, Telegram e TTS continuam sendo dependências externas; falhas nelas precisam ser acompanhadas pelos logs estruturados.
