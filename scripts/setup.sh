#!/usr/bin/env bash
# One-shot dev setup: uv -> venv -> python deps -> frontend deps -> docker perms -> server.
# Idempotent: safe to re-run.
#
# Usage: scripts/setup.sh [--no-start] [--no-frontend] [--no-migrate]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

START_SERVER=true
DO_FRONTEND=true
DO_MIGRATE=true
for arg in "$@"; do
  case "$arg" in
    --no-start)    START_SERVER=false ;;
    --no-frontend) DO_FRONTEND=false ;;
    --no-migrate)  DO_MIGRATE=false ;;
    -h|--help)
      sed -n '2,6p' "$0"; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

# 1. uv ----------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || { err "uv install failed; add ~/.local/bin to PATH and retry"; exit 1; }
log "uv $(uv --version | awk '{print $2}')"

# 2. node/npm ----------------------------------------------------------------
if $DO_FRONTEND; then
  if ! command -v npm >/dev/null 2>&1; then
    err "npm not found. Install Node.js 20+ first:"
    err "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
    exit 1
  fi
  log "node $(node --version), npm $(npm --version)"
fi

# 3. venv + python deps ------------------------------------------------------
if [[ ! -x .venv/bin/python ]]; then
  log "Creating .venv with Python 3.12..."
  uv venv .venv --python 3.12
fi

log "Installing Python deps (editable + dev extras)..."
uv pip install --python .venv/bin/python -e ".[dev]"

# 4. frontend deps -----------------------------------------------------------
if $DO_FRONTEND; then
  log "Installing frontend deps..."
  (cd frontend && npm install)
fi

# 5. db migrations -----------------------------------------------------------
if $DO_MIGRATE; then
  log "Running alembic migrations..."
  (cd backend && "$REPO_ROOT/.venv/bin/alembic" upgrade head)
fi

# 6. docker group ------------------------------------------------------------
need_newgrp=false
if ! getent group docker >/dev/null; then
  warn "docker group missing — is Docker installed? Skipping permissions step."
elif id -nG | tr ' ' '\n' | grep -qx docker; then
  log "User already in docker group."
else
  log "Adding $USER to docker group (sudo)..."
  sudo usermod -aG docker "$USER"
  need_newgrp=true
fi

# 7. start server ------------------------------------------------------------
if ! $START_SERVER; then
  log "Setup complete."
  $need_newgrp && warn "Log out + back in (or run: newgrp docker) before starting the server."
  exit 0
fi

log "Starting backend on http://0.0.0.0:8088 (Ctrl-C to stop)..."
cd backend
UVICORN=("$REPO_ROOT/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8088 --reload --reload-dir app)

if $need_newgrp; then
  warn "Using 'sg docker' so this process inherits the new group without re-login."
  exec sg docker -c "$(printf '%q ' "${UVICORN[@]}")"
else
  exec "${UVICORN[@]}"
fi
