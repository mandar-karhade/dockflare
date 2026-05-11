#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# deploy.sh — Deploy Dockflare to a remote host via rsync + Docker Compose.
#
# Run from your dev machine (Mac/Linux). Rsyncs the repo, ships .env, builds
# and starts the compose stack, then tails backend logs.
#
# Usage:
#   ./deploy.sh                  # Full deploy (rsync + build + up)
#   ./deploy.sh --setup          # First-time host check (Docker / app dir)
#   ./deploy.sh --sync-only      # Rsync only — skip build
#   ./deploy.sh backend          # Rebuild & restart backend only
#   ./deploy.sh frontend         # Rebuild & restart frontend only
#
# Environment overrides:
#   DOCKFLARE_HOST=user@host     (default: m@192.168.0.171)
#   DOCKFLARE_APP_DIR=/path      (default: /home/m/dockflare)
# =============================================================================

DEPLOY_HOST="${DOCKFLARE_HOST:-m@192.168.0.171}"
DEPLOY_APP_DIR="${DOCKFLARE_APP_DIR:-/home/m/dockflare}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPONENT=""
for arg in "$@"; do
  case "$arg" in
    --setup|--sync-only) ;;
    backend|frontend) COMPONENT="$arg" ;;
  esac
done

log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

# ---- Pre-flight ---------------------------------------------------------
[ -f docker/compose.yml ] || err "docker/compose.yml missing — run from repo root."
[ -f .env ]               || err ".env missing at repo root (need CF_TOKEN=...)."

if [ "${1:-}" != "--setup" ]; then
  ssh -o ConnectTimeout=5 -o BatchMode=yes "$DEPLOY_HOST" "echo ok" >/dev/null 2>&1 \
    || err "Cannot SSH into $DEPLOY_HOST (key auth, host reachable?)."
  log "SSH OK."
fi

if [ "${1:-}" = "--setup" ]; then
  log "Checking remote host $DEPLOY_HOST..."
  ssh "$DEPLOY_HOST" "command -v docker && docker compose version" >/dev/null 2>&1 \
    || err "Docker / Compose not available on $DEPLOY_HOST."
  ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_APP_DIR'"
  log "Remote ready. App dir: $DEPLOY_APP_DIR"
  exit 0
fi

# ---- Confirmation -------------------------------------------------------
echo ""
echo "  Deploying Dockflare"
echo "  Host: $DEPLOY_HOST:$DEPLOY_APP_DIR"
[ -n "$COMPONENT" ] && echo "  Component: $COMPONENT (rebuild only this service)"
echo ""
read -p "  Continue? [Y/n] " -n 1 -r
echo ""
[[ $REPLY =~ ^[Nn]$ ]] && { log "Aborted."; exit 0; }

# ---- Rsync (allowlist) -------------------------------------------------
log "Syncing to $DEPLOY_HOST..."
rsync -avz --delete \
  --exclude='.git/' \
  --exclude='.claude/' \
  --exclude='.venv/' \
  --exclude='node_modules/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='.DS_Store' \
  --exclude='backend/data/' \
  --exclude='backend/tm.db*' \
  --exclude='frontend/dist/' \
  --include='backend/' \
  --include='backend/app/***' \
  --include='backend/alembic/***' \
  --include='backend/alembic.ini' \
  --include='backend/tests/***' \
  --include='frontend/' \
  --include='frontend/package.json' \
  --include='frontend/package-lock.json' \
  --include='frontend/tsconfig*.json' \
  --include='frontend/vite.config.ts' \
  --include='frontend/index.html' \
  --include='frontend/src/***' \
  --include='frontend/public/***' \
  --include='docker/***' \
  --include='pyproject.toml' \
  --include='README.md' \
  --exclude='*' \
  "$SCRIPT_DIR/" "$DEPLOY_HOST:$DEPLOY_APP_DIR/"
log "Rsync done."

# ---- Ship .env separately ----------------------------------------------
log "Deploying .env..."
scp "$SCRIPT_DIR/.env" "$DEPLOY_HOST:$DEPLOY_APP_DIR/.env"

if [ "${1:-}" = "--sync-only" ]; then
  log "Sync-only — skipping build."
  exit 0
fi

# ---- Remote build + up -------------------------------------------------
log "Remote build & up..."
ssh "$DEPLOY_HOST" bash -s "$DEPLOY_APP_DIR" "${COMPONENT:-all}" << 'REMOTE_SCRIPT'
  set -euo pipefail
  APP_DIR="$1"; COMPONENT="$2"
  cd "$APP_DIR"
  set -a; source .env; set +a

  COMPOSE="docker compose -f docker/compose.yml"

  if [ "$COMPONENT" != "all" ]; then
    $COMPOSE build "$COMPONENT"
    $COMPOSE up -d --force-recreate "$COMPONENT"
  else
    $COMPOSE build
    $COMPOSE up -d
  fi
  sleep 3
  $COMPOSE ps
REMOTE_SCRIPT

REMOTE_IP="${DEPLOY_HOST#*@}"
echo ""
log "Deployment done."
echo "  UI: http://$REMOTE_IP:8088"
echo ""
log "Streaming backend logs (Ctrl+C to stop — containers keep running)..."
ssh "$DEPLOY_HOST" "cd '$DEPLOY_APP_DIR' && docker compose -f docker/compose.yml logs -f --tail=30 backend"
