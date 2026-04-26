#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_cloudflare_tunnel.sh
# Instala cloudflared e cria um túnel HTTPS persistente para a API Profeta.
#
# Uso:
#   ./scripts/setup_cloudflare_tunnel.sh       # instalação completa com systemd
#   QUICK=1 ./scripts/setup_cloudflare_tunnel.sh  # túnel temporário (trycloudflare)
#
# Após a execução, exporte a URL obtida para o .env:
#   PUBLIC_BASE_URL=https://SEU_SUBDOMINIO.trycloudflare.com
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

API_PORT="${API_PORT:-8000}"
CLOUDFLARED_BIN="/usr/local/bin/cloudflared"
TUNNEL_SERVICE="profeta-tunnel"

# ─── Instalar cloudflared ─────────────────────────────────────────────────────
install_cloudflared() {
    if command -v cloudflared >/dev/null 2>&1; then
        echo "[tunnel] cloudflared já instalado: $(cloudflared --version)"
        return 0
    fi

    echo "[tunnel] Instalando cloudflared..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="linux-amd64" ;;
        aarch64) CF_ARCH="linux-arm64" ;;
        *)        echo "[tunnel] Arquitetura não suportada: $ARCH" >&2; exit 1 ;;
    esac

    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${CF_ARCH}"
    sudo curl -fsSL "$CF_URL" -o "$CLOUDFLARED_BIN"
    sudo chmod +x "$CLOUDFLARED_BIN"
    echo "[tunnel] cloudflared instalado: $($CLOUDFLARED_BIN --version)"
}

# ─── Modo rápido: trycloudflare (sem login, URLs temporárias) ─────────────────
quick_tunnel() {
    echo ""
    echo "[tunnel] ╔══════════════════════════════════════════════════════════╗"
    echo "[tunnel] ║  TÚNEL TEMPORÁRIO (trycloudflare.com)                   ║"
    echo "[tunnel] ║  URL muda a cada execução. Ideal para testes de webhook. ║"
    echo "[tunnel] ╚══════════════════════════════════════════════════════════╝"
    echo ""
    echo "[tunnel] Iniciando túnel para localhost:${API_PORT}..."
    echo "[tunnel] A URL HTTPS aparecerá abaixo em ~5 segundos."
    echo "[tunnel] Pressione Ctrl+C quando terminar."
    echo ""
    cloudflared tunnel --url "http://localhost:${API_PORT}" 2>&1
}

# ─── Modo persistente: túnel com systemd ─────────────────────────────────────
persistent_tunnel() {
    echo "[tunnel] Configurando túnel persistente com systemd..."

    if [[ ! -f ~/.cloudflared/cert.pem ]]; then
        echo "[tunnel] Fazendo login no Cloudflare..."
        cloudflared tunnel login
    fi

    EXISTING=$(cloudflared tunnel list 2>/dev/null | grep "profeta" | awk '{print $1}') || true
    if [[ -n "$EXISTING" ]]; then
        echo "[tunnel] Túnel 'profeta' já existe: $EXISTING"
        TUNNEL_ID="$EXISTING"
    else
        echo "[tunnel] Criando túnel 'profeta'..."
        cloudflared tunnel create profeta
        TUNNEL_ID=$(cloudflared tunnel list | grep profeta | awk '{print $1}')
    fi

    echo "[tunnel] Tunnel ID: $TUNNEL_ID"

    # Criar config
    mkdir -p ~/.cloudflared
    cat > ~/.cloudflared/config.yml << EOF
tunnel: $TUNNEL_ID
credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json

ingress:
  - service: http://localhost:${API_PORT}
EOF

    echo "[tunnel] Config criada em ~/.cloudflared/config.yml"

    # Instalar como serviço systemd
    sudo cloudflared service install
    sudo systemctl enable cloudflared
    sudo systemctl start cloudflared

    echo ""
    echo "[tunnel] Serviço cloudflared ativo."
    echo "[tunnel] Para associar um domínio real, execute:"
    echo "  cloudflared tunnel route dns profeta SEU_SUBDOMINIO.SEU_DOMINIO.com"
    echo ""
    echo "[tunnel] Para testar, use trycloudflare primeiro (QUICK=1 ./setup_cloudflare_tunnel.sh)"
}

# ─── Entry point ─────────────────────────────────────────────────────────────
install_cloudflared

if [[ "${QUICK:-0}" == "1" ]]; then
    quick_tunnel
else
    persistent_tunnel
fi
