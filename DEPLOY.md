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

Alternativa com script de bootstrap (recomendado):

```bash
chmod +x scripts/bootstrap_vps.sh
./scripts/bootstrap_vps.sh
```

## Variáveis obrigatórias para produção

- `ENV=prod`
- `TELEGRAM_BOT_TOKEN`
- `BOT_USERNAME`
- `DATABASE_URL`
- `PUBLIC_BASE_URL`
- `ASAAS_WEBHOOK_TOKEN`

Variáveis recomendadas:

- `ASAAS_PAYMENT_LINK_ID`
- `ASAAS_PAYMENT_LINK_URL`
- `OPENAI_API_KEY`
- `LOG_LEVEL=INFO`

## Preflight antes de subir

```bash
source .venv/bin/activate
python scripts/preflight_check.py
```

Em `ENV=prod`, o comando deve terminar com `preflight.status OK`.

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
- [deploy/systemd/profeta-job.service](deploy/systemd/profeta-job.service)
- [deploy/systemd/profeta-job.timer](deploy/systemd/profeta-job.timer)

Instalação típica:

```bash
sudo cp deploy/systemd/profeta-api.service /etc/systemd/system/
sudo cp deploy/systemd/profeta-bot.service /etc/systemd/system/
sudo cp deploy/systemd/profeta-job.service /etc/systemd/system/
sudo cp deploy/systemd/profeta-job.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now profeta-api
sudo systemctl enable --now profeta-bot
sudo systemctl enable --now profeta-job.timer
```

Validação de sintaxe das units:

```bash
sudo systemd-analyze verify /etc/systemd/system/profeta-api.service
sudo systemd-analyze verify /etc/systemd/system/profeta-bot.service
sudo systemd-analyze verify /etc/systemd/system/profeta-job.service
sudo systemd-analyze verify /etc/systemd/system/profeta-job.timer
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

Job diário:

```bash
journalctl -u profeta-job -f
systemctl list-timers | grep profeta-job
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
sudo systemctl restart profeta-job.timer
```

## Smoke deploy (procedimento mínimo)

1. Preflight:

```bash
source .venv/bin/activate
python scripts/preflight_check.py
```

2. Subir serviços:

```bash
sudo systemctl restart profeta-api
sudo systemctl restart profeta-bot
```

3. Validar API:

```bash
curl -sS http://127.0.0.1:8000/health
```

4. Validar status:

```bash
systemctl status profeta-api --no-pager
systemctl status profeta-bot --no-pager
```

5. Validar logs de startup:

```bash
journalctl -u profeta-api -n 100 --no-pager
journalctl -u profeta-bot -n 100 --no-pager
```

6. Validar fluxo mínimo do produto:

- no Telegram, enviar `/start` e `/versiculo`
- confirmar ausência de crash no `journalctl`

## Rollback simples

```bash
cd /opt/profeta/current
git log --oneline -n 5
git checkout <commit-anterior-estável>
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart profeta-api
sudo systemctl restart profeta-bot
```

## Riscos operacionais conhecidos

- SQLite é suficiente para operação inicial, mas concorrência alta pode exigir migração futura para Postgres.
- OpenAI, Telegram e TTS continuam sendo dependências externas; falhas nelas precisam ser acompanhadas pelos logs estruturados.
