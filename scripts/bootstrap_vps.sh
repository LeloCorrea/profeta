#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-profeta}"
APP_GROUP="${APP_GROUP:-profeta}"
APP_BASE_DIR="${APP_BASE_DIR:-/opt/profeta}"
APP_DIR="${APP_DIR:-$APP_BASE_DIR/current}"
PYTHON_BIN="${PYTHON_BIN:-python3.9}"

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
sudo -u "$APP_USER" mkdir -p "$APP_DIR/logs" "$APP_DIR/data" "$APP_DIR/data/audio" "$APP_DIR/data/audio_cache"

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
