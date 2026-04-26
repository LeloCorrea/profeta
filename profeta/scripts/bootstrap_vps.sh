#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-profeta}"
APP_GROUP="${APP_GROUP:-profeta}"
APP_BASE_DIR="${APP_BASE_DIR:-/opt/profeta}"
APP_DIR="${APP_DIR:-$APP_BASE_DIR/current}"

# Ubuntu 22.04 ships python3.10; aceita Python 3.9+ (nosso código é compatível).
# Sobrescreva com PYTHON_BIN=python3.9 se precisar de versão específica.
if [[ -z "${PYTHON_BIN:-}" ]]; then
  for _candidate in python3.10 python3.9 python3.11 python3; do
    if command -v "$_candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$_candidate"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "[bootstrap] ERRO: nenhum Python 3.x encontrado. Instale com: sudo apt-get install python3 python3-venv" >&2
  exit 1
fi

echo "[bootstrap] Python detectado: $($PYTHON_BIN --version 2>&1)"

echo "[bootstrap] criando usuário/grupo se necessário"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir "$APP_BASE_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

sudo mkdir -p "$APP_BASE_DIR" "$APP_DIR"
sudo chown -R "$APP_USER:$APP_GROUP" "$APP_BASE_DIR"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "[bootstrap] clone do repositório não encontrado em $APP_DIR"
  echo "[bootstrap] faça clone manual e execute novamente"
  exit 1
fi

echo "[bootstrap] preparando diretórios de runtime"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/logs" "$APP_DIR/data" "$APP_DIR/data/audio"

echo "[bootstrap] criando venv"
sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"

echo "[bootstrap] instalando dependências"
sudo -u "$APP_USER" /bin/bash -lc "source '$APP_DIR/.venv/bin/activate'; cd '$APP_DIR'; pip install --upgrade pip; pip install -r requirements.txt"

echo "[bootstrap] se .env não existir, copie de .env.example"
if [[ ! -f "$APP_DIR/.env" ]]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "[bootstrap] .env criado em branco; preencha variáveis obrigatórias antes de subir serviços"
fi

echo "[bootstrap] pronto"
