# SYNOPSIS
#   Deploy automatizado do Profeta para uma VPS Linux (Oracle Cloud / Ubuntu 22.04).
#
# DESCRIPTION
#   Este script:
#     1. Verifica conectividade SSH com a VPS
#     2. Instala Python e dependências de sistema
#     3. Cria usuário/grupo profeta
#     4. Clona ou atualiza o repositório
#     5. Cria venv e instala dependências Python
#     6. Obtém ou verifica o arquivo .env
#     7. Executa preflight e exige aprovação antes de continuar
#     8. Instala e ativa os serviços systemd
#     9. Executa smoke test de saúde
#
# PARAMETERS
#   -VpsIp    IP público da VPS. Exemplo: 129.146.90.12
#   -SshUser  Usuário SSH (padrão: ubuntu)
#   -SshKey   Caminho local para a chave SSH PEM
#   -RepoUrl  URL do repositório Git
#   -Branch   Branch a fazer checkout
#   -EnvFile  Caminho local do arquivo .env a ser enviado para a VPS
#
# EXAMPLES
#   .\scripts\deploy_to_vps.ps1 -VpsIp 129.146.90.12
#   .\scripts\deploy_to_vps.ps1 -VpsIp 129.146.90.12 -EnvFile C:\segredos\profeta.env

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VpsIp,

    [string]$SshUser = "ubuntu",

    [string]$SshKey = "$env:USERPROFILE\.ssh\agente-renata.pem",

    [string]$RepoUrl = "https://github.com/LeloCorrea/profeta",

    [string]$Branch = "main",

    [string]$EnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
$SSH_OPTS = "-i `"$SshKey`" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes"
$SSH_TARGET = "${SshUser}@${VpsIp}"

function Write-Step {
    param([string]$Msg)
    Write-Host "`n━━━ $Msg ━━━" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Msg)
    Write-Host "  ✓ $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  ⚠ $Msg" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "  ✗ $Msg" -ForegroundColor Red
}

function Invoke-SSH {
    param(
        [string]$Command,
        [string]$Description,
        [switch]$AllowFail
    )
    Write-Host "  » $Description" -ForegroundColor DarkGray
    $output = Invoke-Expression "ssh $SSH_OPTS $SSH_TARGET '$Command'" 2>&1
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        Write-Fail "FALHOU (exit $LASTEXITCODE): $Description"
        Write-Host $output -ForegroundColor DarkRed
        throw "SSH command failed: $Description"
    }
    return $output
}

function Invoke-SSHHeredoc {
    param(
        [string]$Script,
        [string]$Description
    )
    Write-Host "  » $Description" -ForegroundColor DarkGray
    $tmpFile = New-TemporaryFile
    $Script | Set-Content -Path $tmpFile.FullName -Encoding UTF8
    # Convert to LF line endings
    $content = [System.IO.File]::ReadAllText($tmpFile.FullName) -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($tmpFile.FullName, $content)

    $scriptContent = Get-Content -Path $tmpFile.FullName -Raw
    $scriptContent | & ssh $SSH_OPTS.Split(" ") $SSH_TARGET "bash -s"
    $exitCode = $LASTEXITCODE
    Remove-Item $tmpFile.FullName -Force
    if ($exitCode -ne 0) {
        Write-Fail "FALHOU (exit $exitCode): $Description"
        throw "SSH heredoc failed: $Description"
    }
}

# ─────────────────────────────────────────────────────────────
# ETAPA 1 — Verificar chave SSH e conectividade
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 1 — Verificação de conectividade"

if (-not (Test-Path $SshKey)) {
    Write-Fail "Chave SSH não encontrada: $SshKey"
    Write-Host "  Coloque a chave PEM da Oracle Cloud em: $SshKey"
    exit 1
}
Write-Ok "Chave SSH: $SshKey"

Write-Host "  » Testando conectividade com $VpsIp..." -ForegroundColor DarkGray
$ping = Test-Connection -ComputerName $VpsIp -Count 2 -Quiet
if (-not $ping) {
    Write-Warn "ICMP bloqueado (normal em Oracle Cloud). Tentando SSH diretamente..."
}

$whoami = Invoke-SSH "whoami" "Verificar acesso SSH"
Write-Ok "Conectado como: $($whoami.Trim())"

$osInfo = Invoke-SSH "uname -r && cat /etc/os-release | grep -E '^(NAME|VERSION)='" "Verificar OS"
Write-Ok "OS:`n$osInfo"

# ─────────────────────────────────────────────────────────────
# ETAPA 2 — Dependências de sistema
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 2 — Dependências de sistema"

Invoke-SSHHeredoc @'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "[sys] Atualizando lista de pacotes..."
sudo apt-get update -qq

echo "[sys] Instalando dependências..."
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip \
    git curl wget \
    libssl-dev \
    2>&1 | tail -5

echo "[sys] Python: $(python3 --version)"
echo "[sys] Git: $(git --version)"
'@ "Instalar Python e dependências de sistema"

Write-Ok "Dependências instaladas"

# ─────────────────────────────────────────────────────────────
# ETAPA 3 — Usuário/grupo profeta
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 3 — Usuário profeta"

Invoke-SSHHeredoc @'
set -euo pipefail
if ! id -u profeta >/dev/null 2>&1; then
    sudo useradd --system --create-home --home-dir /opt/profeta \
        --shell /usr/sbin/nologin profeta
    echo "[user] Usuário profeta criado"
else
    echo "[user] Usuário profeta já existe"
fi
sudo mkdir -p /opt/profeta/current
sudo chown -R profeta:profeta /opt/profeta
'@ "Criar usuário profeta"

Write-Ok "Usuário profeta configurado"

# ─────────────────────────────────────────────────────────────
# ETAPA 4 — Clone ou update do repositório
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 4 — Repositório"

Invoke-SSHHeredoc @"
set -euo pipefail
APP_DIR=/opt/profeta/current
REPO_URL=$RepoUrl
BRANCH=$Branch

if [[ -d `"`$APP_DIR/.git`" ]]; then
    echo '[repo] Atualizando repositório existente...'
    sudo -u profeta git -C `"`$APP_DIR`" fetch origin
    sudo -u profeta git -C `"`$APP_DIR`" checkout `"`$BRANCH`"
    sudo -u profeta git -C `"`$APP_DIR`" reset --hard origin/`"`$BRANCH`"
else
    echo '[repo] Clonando repositório...'
    sudo -u profeta git clone --branch `"`$BRANCH`" `"`$REPO_URL`" `"`$APP_DIR`"
fi

echo '[repo] Commit atual:'
sudo -u profeta git -C `"`$APP_DIR`" log --oneline --decorate -1
"@ "Clonar/atualizar repositório"

Write-Ok "Repositório sincronizado"

# ─────────────────────────────────────────────────────────────
# ETAPA 5 — Ambiente virtual Python
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 5 — Virtual environment e dependências Python"

Invoke-SSHHeredoc @'
set -euo pipefail
APP_DIR=/opt/profeta/current

if [[ ! -d "$APP_DIR/.venv" ]]; then
    echo "[venv] Criando venv..."
    sudo -u profeta python3 -m venv "$APP_DIR/.venv"
fi

echo "[venv] Instalando dependências Python..."
sudo -u profeta bash -lc "
    source '$APP_DIR/.venv/bin/activate'
    pip install --upgrade pip --quiet
    pip install -r '$APP_DIR/requirements.txt' --quiet
    echo '[venv] Pacotes instalados:'
    pip list --format=columns | grep -E '(fastapi|uvicorn|telegram|sqlalchemy|openai|edge)'
"
'@ "Criar venv e instalar requirements"

Write-Ok "Venv e dependências prontos"

# ─────────────────────────────────────────────────────────────
# ETAPA 6 — Arquivo .env
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 6 — Configuração .env"

$envExists = Invoke-SSH "test -f /opt/profeta/current/.env && echo exists || echo missing" "Verificar .env" -AllowFail

if ($EnvFile -ne "" -and (Test-Path $EnvFile)) {
    Write-Host "  » Enviando .env local para a VPS..." -ForegroundColor DarkGray
    & scp $SSH_OPTS.Split(" ") $EnvFile "${SSH_TARGET}:/tmp/profeta.env.tmp"
    if ($LASTEXITCODE -ne 0) { throw "SCP falhou ao enviar .env" }
    Invoke-SSH "sudo mv /tmp/profeta.env.tmp /opt/profeta/current/.env && sudo chown profeta:profeta /opt/profeta/current/.env && sudo chmod 600 /opt/profeta/current/.env" "Instalar .env"
    Write-Ok ".env enviado e instalado (modo 600)"
} elseif ($envExists.Trim() -eq "exists") {
    Write-Ok ".env já existe na VPS"
} else {
    Invoke-SSH "sudo -u profeta cp /opt/profeta/current/.env.example /opt/profeta/current/.env && sudo chmod 600 /opt/profeta/current/.env" "Criar .env a partir do exemplo"
    Write-Warn ".env criado a partir do exemplo."
    Write-Warn "AÇÃO OBRIGATÓRIA: preencha os valores reais antes de continuar!"
    Write-Host ""
    Write-Host "  Edite o .env na VPS:" -ForegroundColor Yellow
    Write-Host "    ssh -i `"$SshKey`" $SSH_TARGET" -ForegroundColor White
    Write-Host "    sudo nano /opt/profeta/current/.env" -ForegroundColor White
    Write-Host ""
    Write-Host "  Variáveis obrigatórias:" -ForegroundColor Yellow
    Write-Host "    TELEGRAM_BOT_TOKEN, BOT_USERNAME, PUBLIC_BASE_URL," -ForegroundColor White
    Write-Host "    ASAAS_WEBHOOK_TOKEN, OPENAI_API_KEY" -ForegroundColor White
    Write-Host ""
    $resp = Read-Host "  Pressione ENTER quando o .env estiver preenchido, ou 's' para sair"
    if ($resp.ToLower() -eq "s") { exit 0 }
}

# ─────────────────────────────────────────────────────────────
# ETAPA 7 — Preflight check
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 7 — Preflight check"

$preflight = Invoke-SSH @"
cd /opt/profeta/current
source .venv/bin/activate
set -a; source .env; set +a
python scripts/preflight_check.py
"@ "Executar preflight"

Write-Host $preflight -ForegroundColor DarkGray

if ($preflight -match "preflight\.missing") {
    Write-Fail "Preflight FALHOU — variáveis ausentes!"
    Write-Host "  Corrija o .env e execute o script novamente." -ForegroundColor Red
    exit 1
}
Write-Ok "Preflight aprovado"

# ─────────────────────────────────────────────────────────────
# ETAPA 8 — Diretórios de runtime e permissões
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 8 — Diretórios de runtime"

Invoke-SSHHeredoc @'
set -euo pipefail
APP_DIR=/opt/profeta/current
sudo -u profeta mkdir -p \
    "$APP_DIR/logs" \
    "$APP_DIR/data" \
    "$APP_DIR/data/audio"
sudo chmod +x "$APP_DIR/scripts/"*.sh
echo "[dirs] OK:"
ls -la "$APP_DIR/data/" "$APP_DIR/logs/"
'@ "Criar diretórios e definir permissões"

Write-Ok "Diretórios criados"

# ─────────────────────────────────────────────────────────────
# ETAPA 9 — Systemd services
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 9 — Serviços systemd"

Invoke-SSHHeredoc @'
set -euo pipefail
APP_DIR=/opt/profeta/current

echo "[systemd] Instalando units..."
sudo cp "$APP_DIR/deploy/systemd/profeta-api.service"  /etc/systemd/system/
sudo cp "$APP_DIR/deploy/systemd/profeta-bot.service"  /etc/systemd/system/
sudo cp "$APP_DIR/deploy/systemd/profeta-job.service"  /etc/systemd/system/
sudo cp "$APP_DIR/deploy/systemd/profeta-job.timer"    /etc/systemd/system/

echo "[systemd] Verificando units..."
sudo systemd-analyze verify /etc/systemd/system/profeta-api.service 2>&1 || true
sudo systemd-analyze verify /etc/systemd/system/profeta-bot.service 2>&1 || true

echo "[systemd] Recarregando daemon..."
sudo systemctl daemon-reload

echo "[systemd] Habilitando serviços..."
sudo systemctl enable profeta-api profeta-bot profeta-job.timer

echo "[systemd] Iniciando serviços..."
sudo systemctl start profeta-api profeta-bot profeta-job.timer

sleep 5

echo "[systemd] Status:"
sudo systemctl status profeta-api --no-pager --lines=5
sudo systemctl status profeta-bot --no-pager --lines=5
sudo systemctl list-timers profeta-job.timer --no-pager
'@ "Instalar e iniciar serviços systemd"

Write-Ok "Serviços systemd ativados"

# ─────────────────────────────────────────────────────────────
# ETAPA 10 — Firewall (ufw)
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 10 — Firewall"

Invoke-SSHHeredoc @'
set -euo pipefail
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 22/tcp   comment "SSH" 2>/dev/null || true
    sudo ufw allow 8000/tcp comment "Profeta API" 2>/dev/null || true
    echo "[fw] ufw rules:"
    sudo ufw status numbered 2>/dev/null || true
else
    echo "[fw] ufw não disponível — verifique Security Lists na Oracle Cloud"
fi
'@ "Configurar firewall local"

Write-Ok "Firewall verificado"

# ─────────────────────────────────────────────────────────────
# ETAPA 11 — Smoke test healthcheck
# ─────────────────────────────────────────────────────────────
Write-Step "ETAPA 11 — Smoke test"

Write-Host "  » Aguardando API inicializar (10s)..." -ForegroundColor DarkGray
Start-Sleep -Seconds 10

$healthLocal = Invoke-SSH "curl -sS http://127.0.0.1:8000/health" "Healthcheck local"
Write-Host "  Resposta healthcheck:" -ForegroundColor DarkGray
Write-Host "  $healthLocal" -ForegroundColor White

if ($healthLocal -match '"status":"healthy"') {
    Write-Ok "API respondendo: $healthLocal"
} else {
    Write-Warn "Healthcheck inesperado: $healthLocal"
    Write-Host "  Logs da API:" -ForegroundColor Yellow
    $logs = Invoke-SSH "sudo journalctl -u profeta-api -n 30 --no-pager" "Logs API" -AllowFail
    Write-Host $logs -ForegroundColor DarkGray
}

# Healthcheck externo
Write-Host "  » Testando healthcheck externo..." -ForegroundColor DarkGray
try {
    $resp = Invoke-WebRequest -Uri "http://${VpsIp}:8000/health" -TimeoutSec 10 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
        Write-Ok "Healthcheck externo OK: $($resp.Content)"
    }
} catch {
    Write-Warn "Healthcheck externo falhou — verifique Security List da Oracle Cloud"
    Write-Host "  Regra necessária: Ingress TCP port 8000 source 0.0.0.0/0"
}

# ─────────────────────────────────────────────────────────────
# RESUMO FINAL
# ─────────────────────────────────────────────────────────────
Write-Step "RESUMO DO DEPLOY"

$apiStatus = Invoke-SSH "systemctl is-active profeta-api" "Status API" -AllowFail
$botStatus = Invoke-SSH "systemctl is-active profeta-bot" "Status Bot" -AllowFail
$timerStatus = Invoke-SSH "systemctl is-active profeta-job.timer" "Status Timer" -AllowFail
$commit = Invoke-SSH "git -C /opt/profeta/current log --oneline -1" "Commit" -AllowFail

Write-Host ""
Write-Host "  ┌─────────────────────────────────────────────┐" -ForegroundColor White
Write-Host "  │         RESULTADO DO DEPLOY                 │" -ForegroundColor White
Write-Host "  ├─────────────────────────────────────────────┤" -ForegroundColor White
Write-Host "  │ profeta-api.service  : $($apiStatus.Trim().PadRight(20)) │" -ForegroundColor White
Write-Host "  │ profeta-bot.service  : $($botStatus.Trim().PadRight(20)) │" -ForegroundColor White
Write-Host "  │ profeta-job.timer    : $($timerStatus.Trim().PadRight(20)) │" -ForegroundColor White
Write-Host "  │ Commit               : $($commit.Trim().Substring(0, [Math]::Min(20, $commit.Trim().Length)).PadRight(20)) │" -ForegroundColor White
Write-Host "  │ API URL              : http://${VpsIp}:8000$(" " * [Math]::Max(0, 18 - $VpsIp.Length)) │" -ForegroundColor White
Write-Host "  └─────────────────────────────────────────────┘" -ForegroundColor White
Write-Host ""

Write-Host "Próximos passos obrigatórios:" -ForegroundColor Yellow
Write-Host "  1. Configure Cloudflare Tunnel para expor a API com HTTPS"
Write-Host "     (necessário para webhook Asaas e PUBLIC_BASE_URL)"
Write-Host "  2. Atualize PUBLIC_BASE_URL no .env com a URL real do túnel"
Write-Host "  3. Teste o bot no Telegram: /start /versiculo /explicar"
Write-Host "  4. Configure o webhook no Asaas apontando para:"
Write-Host "     https://SEU_DOMINIO/webhooks/asaas"
Write-Host ""
Write-Host "Comandos úteis na VPS:" -ForegroundColor Cyan
Write-Host "  sudo journalctl -u profeta-api -f"
Write-Host "  sudo journalctl -u profeta-bot -f"
Write-Host "  sudo systemctl restart profeta-api profeta-bot"
Write-Host "  curl -sS http://127.0.0.1:8000/health"
