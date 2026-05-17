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
#   ./deploy.sh --host user@ip   # Deploy to a specific SSH host
#   ./deploy.sh --setup          # First-time host check (Docker / app dir)
#   ./deploy.sh --sync-only      # Rsync only — skip build
#   ./deploy.sh backend          # Rebuild & restart backend only
#   ./deploy.sh frontend         # Rebuild & restart frontend only
#
# Environment overrides:
#   DOCKFLARE_HOST=user@host     (default: m@192.168.0.171)
#   DOCKFLARE_APP_DIR=/path      (default: <remote-home>/dockflare)
# =============================================================================

DEPLOY_HOST="${DOCKFLARE_HOST:-m@192.168.0.171}"
DEPLOY_APP_DIR="${DOCKFLARE_APP_DIR:-}"
APP_DIR_EXPLICIT=0
[ -n "${DOCKFLARE_APP_DIR:-}" ] && APP_DIR_EXPLICIT=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage:
  ./deploy.sh [--host user@ip] [--app-dir /path] [--setup|--sync-only] [backend|frontend]

Options:
  --host user@ip     SSH target host (default: ${DOCKFLARE_HOST:-m@192.168.0.171})
  --app-dir /path    Remote app directory (default: <remote-home>/dockflare)
  --setup            First-time host check (Docker / app dir)
  --sync-only        Rsync only — skip build
  -h, --help         Show this help
EOF
}

MODE="deploy"
COMPONENT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || err "--host requires a value like sharedservices@152.53.84.146"
      DEPLOY_HOST="$2"
      shift 2
      ;;
    --host=*)
      DEPLOY_HOST="${1#--host=}"
      shift
      ;;
    --app-dir)
      [ "$#" -ge 2 ] || err "--app-dir requires a remote path"
      DEPLOY_APP_DIR="$2"
      APP_DIR_EXPLICIT=1
      shift 2
      ;;
    --app-dir=*)
      DEPLOY_APP_DIR="${1#--app-dir=}"
      APP_DIR_EXPLICIT=1
      shift
      ;;
    --setup)
      MODE="setup"
      shift
      ;;
    --sync-only)
      MODE="sync-only"
      shift
      ;;
    backend|frontend)
      COMPONENT="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      ;;
  esac
done

[ -n "$DEPLOY_HOST" ] || err "Deploy host cannot be empty."

resolve_default_app_dir() {
  if [ "$APP_DIR_EXPLICIT" -eq 1 ]; then
    [ -n "$DEPLOY_APP_DIR" ] || err "Deploy app dir cannot be empty."
    return
  fi

  REMOTE_HOME="$(ssh "$DEPLOY_HOST" 'printf %s "$HOME"')" \
    || err "Cannot resolve remote home directory on $DEPLOY_HOST."
  [ -n "$REMOTE_HOME" ] || err "Remote home directory is empty on $DEPLOY_HOST."
  DEPLOY_APP_DIR="$REMOTE_HOME/dockflare"
}

# ---- Pre-flight ---------------------------------------------------------
[ -f docker/compose.yml ] || err "docker/compose.yml missing — run from repo root."
[ -f .env ]               || err ".env missing at repo root (need CF_TOKEN=...)."

if [ "$MODE" != "setup" ]; then
  ssh -o ConnectTimeout=5 -o BatchMode=yes "$DEPLOY_HOST" "echo ok" >/dev/null 2>&1 \
    || err "Cannot SSH into $DEPLOY_HOST (key auth, host reachable?)."
  log "SSH OK."
  resolve_default_app_dir
  ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_APP_DIR'" \
    || err "Cannot create app dir $DEPLOY_APP_DIR on $DEPLOY_HOST."
fi

if [ "$MODE" = "setup" ]; then
  log "Checking remote host $DEPLOY_HOST..."
  ssh "$DEPLOY_HOST" "command -v docker && docker compose version" >/dev/null 2>&1 \
    || err "Docker / Compose not available on $DEPLOY_HOST."
  resolve_default_app_dir
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

if [ "$MODE" = "sync-only" ]; then
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
ssh "$DEPLOY_HOST" "cd '$DEPLOY_APP_DIR' && set -a && source .env && docker compose -f docker/compose.yml logs -f --tail=30 backend"
