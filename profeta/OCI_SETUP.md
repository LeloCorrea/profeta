# Oracle Cloud Free Tier — Guia de Provisionamento e Deploy

Este guia cobre a criação de uma VM gratuita na Oracle Cloud (Always Free),
o deploy do Profeta com systemd, e a exposição da API via Cloudflare Tunnel.

---

## 1. Pré-requisitos

| Item | Requisito |
|---|---|
| Conta Oracle Cloud | [cloud.oracle.com](https://cloud.oracle.com) — gratuita |
| Chave SSH | `~/.ssh/agente-renata.pem` já existente |
| PowerShell | Windows 10/11 com OpenSSH |
| Git | repositório sincronizado em `main` |
| cloudflared | instalado na VPS (o script `setup_cloudflare_tunnel.sh` faz isso) |

---

## 2. Criar a Instância Always Free

### 2.1 Acessar o Console

1. Faça login em [cloud.oracle.com](https://cloud.oracle.com)
2. Menu hamburger (☰) → **Compute** → **Instances** → **Create Instance**

### 2.2 Configurações da Instância

| Campo | Valor |
|---|---|
| **Name** | `profeta-prod` |
| **Compartment** | seu compartimento padrão |
| **Availability domain** | qualquer (ex: AD-1) |

**Image and shape:**
1. Clique em **Edit** → **Change image**
2. Selecione: **Ubuntu** → **Ubuntu 22.04 Minimal** → Confirm
3. Clique em **Change shape**
4. Shape series: **AMD** → **VM.Standard.E2.1.Micro** (Always Free elegível)

**Networking:**
1. Se for primeira vez, clique em **View VCN Wizard** e aceite os padrões
2. Ou selecione VCN/subnet existente
3. Marque: **Assign a public IPv4 address**

**Add SSH keys:**
1. Selecione: **Upload public key files (.pub)**
2. Clique em Upload → navegue até `C:\Users\SEU_USUARIO\.ssh\`

> ⚠️ Você precisa do arquivo `.pub`, não do `.pem`. Extraia com:
> ```powershell
> # Extrair chave pública do PEM existente
> ssh-keygen -y -f "$env:USERPROFILE\.ssh\agente-renata.pem" | Out-File -Encoding ASCII "$env:USERPROFILE\.ssh\agente-renata.pub"
> cat "$env:USERPROFILE\.ssh\agente-renata.pub"
> ```
> Cole o conteúdo diretamente no campo "SSH key" se preferir.

**Boot volume:** deixe os padrões (50 GB é mais que suficiente).

3. Clique em **Create** e aguarde ~3-5 minutos.

### 2.3 Anotar o IP Público

Após criação, na página da instância:
- **Public IP address** → anote este valor (ex: `129.146.90.12`)

---

## 3. Configurar Ingress Rules (porta 8000)

A Oracle Cloud bloqueia todas as portas por padrão exceto 22.

1. Na instância, clique em **Subnet** link
2. Clique em **Security List** associada
3. **Add Ingress Rules** → adicione:

| Source CIDR | IP Protocol | Port Range | Description |
|---|---|---|---|
| 0.0.0.0/0 | TCP | 8000 | Profeta API |
| 0.0.0.0/0 | TCP | 80 | HTTP (Cloudflare Tunnel) |
| 0.0.0.0/0 | TCP | 443 | HTTPS (Cloudflare Tunnel) |

> Nota: A porta 22 (SSH) já deve estar liberada por padrão.

---

## 4. Preparar o Arquivo .env de Produção

Antes de executar o deploy, crie o arquivo `.env` com os valores reais.

Copie `.env.example` localmente e preencha:

```bash
# Em PowerShell na sua máquina Windows, crie C:\segredos\profeta.env:
copy C:\dev\profeta_saas\profeta\.env.example C:\segredos\profeta.env
notepad C:\segredos\profeta.env
```

### Variáveis obrigatórias de produção:

```bash
APP_NAME=profeta
ENV=prod
LOG_LEVEL=INFO
PORT=8000

# Telegram Bot (obtenha em https://t.me/BotFather)
TELEGRAM_BOT_TOKEN=SEU_TOKEN_REAL
BOT_USERNAME=nome_do_seu_bot  # sem @

# OpenAI (https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-...
OPENAI_EXPLANATION_MODEL=gpt-4o-mini  # ou o modelo que preferir

# Asaas (https://asaas.com/api/reference)
ASAAS_API_KEY=REAL_ASAAS_API_KEY
ASAAS_ENV=production  # trocar de sandbox para production
ASAAS_WEBHOOK_SECRET=REAL_WEBHOOK_SECRET
ASAAS_WEBHOOK_TOKEN=REAL_WEBHOOK_TOKEN
ASAAS_PAYMENT_LINK_ID=REAL_LINK_ID
ASAAS_PAYMENT_LINK_URL=https://www.asaas.com/c/REAL_LINK

# URL pública — preencha após configurar Cloudflare Tunnel
PUBLIC_BASE_URL=https://SEU_SUBDOMINIO.trycloudflare.com

# Fuso horário e notificações
TIMEZONE=America/Sao_Paulo
DAILY_SEND_HOUR=8
```

> ⚠️ **Segurança**: Nunca commite este arquivo. Ele está em `C:\segredos\` (fora do repositório).

---

## 5. Executar o Deploy Automatizado

Com a VPS criada e `.env` preenchido, execute o script de deploy:

```powershell
cd C:\dev\profeta_saas\profeta

# Deploy completo (com envio do .env)
.\scripts\deploy_to_vps.ps1 `
    -VpsIp SEU_IP_DA_VPS `
    -SshUser ubuntu `
    -SshKey "$env:USERPROFILE\.ssh\agente-renata.pem" `
    -EnvFile "C:\segredos\profeta.env"
```

O script realiza automaticamente as Etapas 2-11 da missão de deploy:
- ✅ Instala dependências de sistema
- ✅ Cria usuário `profeta`
- ✅ Clona/atualiza repositório
- ✅ Cria venv e instala requirements
- ✅ Instala e valida o `.env`
- ✅ Executa preflight check
- ✅ Instala e ativa serviços systemd
- ✅ Executa healthcheck local e externo

---

## 6. Configurar Cloudflare Tunnel (webhook + URL pública)

O Asaas exige HTTPS para o webhook. Use Cloudflare Tunnel para isso.

### 6.1 Acessar a VPS via SSH

```powershell
ssh -i "$env:USERPROFILE\.ssh\agente-renata.pem" ubuntu@SEU_IP_DA_VPS
```

### 6.2 Teste rápido (trycloudflare — sem conta Cloudflare)

```bash
# Na VPS:
QUICK=1 sudo -u profeta /opt/profeta/current/scripts/setup_cloudflare_tunnel.sh
```

A URL aparecerá no terminal:
```
https://random-words-here.trycloudflare.com
```

> ⚠️ Esta URL muda a cada execução. Use para testes. Para produção real, use túnel persistente (passo 6.3).

### 6.3 Atualizar PUBLIC_BASE_URL

```bash
# Na VPS, edite o .env:
sudo nano /opt/profeta/current/.env

# Altere a linha:
PUBLIC_BASE_URL=https://SEU_SUBDOMINIO.trycloudflare.com

# Reinicie os serviços:
sudo systemctl restart profeta-api profeta-bot
```

### 6.4 Configurar webhook no Asaas

1. Acesse [asaas.com](https://asaas.com) → **Integrações** → **Webhooks**
2. Adicione webhook:
   - **URL**: `https://SEU_SUBDOMINIO.trycloudflare.com/webhooks/asaas`
   - **Método**: POST
   - **Eventos**: `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`
   - **Access-Token**: valor de `ASAAS_WEBHOOK_TOKEN` no `.env`
3. Teste o webhook e confirme código 200

---

## 7. Validação Funcional no Telegram

Após deploy e Cloudflare Tunnel ativos:

1. Abra o Telegram e encontre seu bot
2. Execute em sequência e verifique cada resposta:

```
/start          → mensagem de boas-vindas
/versiculo      → versículo do dia com áudio
/explicar       → explicação com IA
/meuultimo      → histórico do último versículo
```

3. Monitore os logs em tempo real:
```bash
# Na VPS:
sudo journalctl -u profeta-api -f &
sudo journalctl -u profeta-bot -f
```

---

## 8. Checklist Operacional de Produção

Execute este checklist após o deploy:

```bash
# Na VPS (ssh ubuntu@SEU_IP):

# ── Serviços ──────────────────────────────────────────────────
systemctl is-active profeta-api      # deve retornar: active
systemctl is-active profeta-bot      # deve retornar: active
systemctl is-active profeta-job.timer # deve retornar: active

# ── Healthcheck ───────────────────────────────────────────────
curl -sS http://127.0.0.1:8000/health
# {"status":"healthy","app":"profeta","env":"prod"}

# ── Logs (últimas 20 linhas) ──────────────────────────────────
journalctl -u profeta-api -n 20 --no-pager
journalctl -u profeta-bot -n 20 --no-pager

# ── Timer ─────────────────────────────────────────────────────
systemctl list-timers profeta-job.timer --no-pager
# deve mostrar próxima execução às 08:00

# ── Banco de dados ────────────────────────────────────────────
ls -lh /opt/profeta/current/data/profeta.db
# arquivo deve existir e ter size > 0 após primeiro start

# ── Commit em produção ────────────────────────────────────────
git -C /opt/profeta/current log --oneline -1
```

---

## 9. Rollback

```bash
# Na VPS:
cd /opt/profeta/current

# Ver commits disponíveis
git log --oneline -5

# Voltar para commit específico
git checkout COMMIT_HASH
source .venv/bin/activate
pip install -r requirements.txt

# Reiniciar serviços
sudo systemctl restart profeta-api profeta-bot
```

---

## 10. Troubleshooting Rápido

| Sintoma | Verificação | Correção |
|---|---|---|
| API não sobe | `journalctl -u profeta-api -n 50` | Verificar .env e variáveis |
| Bot não responde | `journalctl -u profeta-bot -n 50` | Verificar TELEGRAM_BOT_TOKEN |
| Webhook retorna 401 | `curl -X POST .../webhooks/asaas -H "asaas-access-token: TOKEN"` | Comparar token no .env e no Asaas |
| Database locked | `systemctl stop profeta-api; systemctl start profeta-api` | Reiniciar resolve |
| Porta 8000 não alcançável | Security List OCI + `sudo ufw status` | Adicionar regra de ingress |
| Versículos não enviados | `systemctl start profeta-job.service` | Executar job manualmente |
| Audio falha | `journalctl -u profeta-bot -n 100 \| grep tts` | edge-tts precisa de internet |

---

## 11. Monitoramento Futuro

Para uma fase posterior, considere:

- **Uptime monitoring**: UptimeRobot ou BetterStack pingando `/health`
- **Alertas Telegram**: script que notifica se API ficar offline
- **Log rotation**: `logrotate` para `/opt/profeta/current/logs/`
- **Backup SQLite**: cron job para copiar `data/profeta.db` para Object Storage OCI
- **PostgreSQL migration**: quando o número de usuários concurrent crescer

---

*Documento gerado em: 2026-04-15 | Versão: d4bb669*
