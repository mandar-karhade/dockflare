#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# deploy.sh — Deploy Dockflare to a remote host via rsync + Docker Compose.
#
# Run from your dev machine (Mac/Linux). Fetches main (or dev) from origin,
# rsyncs a clean snapshot, preserves server config, and provisions HTTPS.
#
# Usage:
#   ./deploy.sh                  # Deploy origin/main (default)
#   ./deploy.sh --main           # Deploy origin/main explicitly
#   ./deploy.sh --dev            # Deploy origin/dev
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
DEPLOY_BRANCH="main"
BRANCH_SELECTED=""
SOURCE_DIR=""
APP_DIR_EXPLICIT=0
[ -n "${DOCKFLARE_APP_DIR:-}" ] && APP_DIR_EXPLICIT=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage:
  ./deploy.sh [--main|--dev] [--host user@ip] [--app-dir /path] [--setup|--sync-only] [backend|frontend]

Options:
  --main            Deploy the latest origin/main commit (default)
  --dev             Deploy the latest origin/dev commit
  --host user@ip     SSH target host (default: ${DOCKFLARE_HOST:-m@192.168.0.171})
  --app-dir /path    Remote app directory (default: <remote-home>/dockflare)
  --setup            First-time host check (Docker / app dir)
  --sync-only        Rsync only — skip build
  -h, --help         Show this help

Only committed, pushed code from the selected origin branch is deployed.
Your current branch and local edits are left untouched. Existing remote .env is preserved.
The local .env is used only on a new server. Each target must use an IPv4 address.
A persistent dockflare-FIRST-xxxx-LAST.abbrv8.com URL is provisioned automatically.
If the selected branch lacks frontend/package-lock.json, npm is required locally
and generates the lockfile from that branch's package.json in a temporary directory.
EOF
}

MODE="deploy"
COMPONENT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev|--main)
      SELECTED="${1#--}"
      [ -z "$BRANCH_SELECTED" ] || [ "$BRANCH_SELECTED" = "$SELECTED" ] \
        || err "--dev and --main cannot be used together."
      DEPLOY_BRANCH="$SELECTED"
      BRANCH_SELECTED="$SELECTED"
      shift
      ;;
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

install_remote_rsync() {
  log "Remote rsync missing; attempting install on $DEPLOY_HOST..."
  ssh -tt "$DEPLOY_HOST" 'bash -lc '"'"'
    set -euo pipefail

    if command -v rsync >/dev/null 2>&1; then
      exit 0
    fi

    SUDO=""
    [ "$(id -u)" -eq 0 ] || SUDO="sudo"

    if command -v apt-get >/dev/null 2>&1; then
      $SUDO apt-get update
      $SUDO apt-get install -y rsync
    elif command -v dnf >/dev/null 2>&1; then
      $SUDO dnf install -y rsync
    elif command -v yum >/dev/null 2>&1; then
      $SUDO yum install -y rsync
    elif command -v apk >/dev/null 2>&1; then
      $SUDO apk add --no-cache rsync
    elif command -v pacman >/dev/null 2>&1; then
      $SUDO pacman -Sy --noconfirm rsync
    elif command -v brew >/dev/null 2>&1; then
      brew install rsync
    else
      echo "No supported package manager found to install rsync." >&2
      exit 1
    fi
  '"'"''
}

ensure_rsync() {
  command -v rsync >/dev/null 2>&1 \
    || err "Local rsync not found. Install rsync on this machine and retry."

  if ! ssh "$DEPLOY_HOST" "command -v rsync" >/dev/null 2>&1; then
    install_remote_rsync \
      || err "Could not install rsync on $DEPLOY_HOST. Install it manually and retry."
  fi

  ssh "$DEPLOY_HOST" "command -v rsync" >/dev/null 2>&1 \
    || err "rsync still not available on $DEPLOY_HOST after install attempt."
  log "Rsync OK."
}

# ---- Pre-flight ---------------------------------------------------------
if [ "$MODE" = "setup" ]; then
  log "Checking remote host $DEPLOY_HOST..."
  ssh "$DEPLOY_HOST" "command -v docker && docker compose version" >/dev/null 2>&1 \
    || err "Docker / Compose not available on $DEPLOY_HOST."
  resolve_default_app_dir
  ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_APP_DIR'"
  ensure_rsync
  log "Remote ready. App dir: $DEPLOY_APP_DIR"
  exit 0
fi

[ -f "$SCRIPT_DIR/.env" ] || err ".env missing beside deploy.sh (need CF_TOKEN=...)."
command -v git >/dev/null 2>&1 || err "Git is required to fetch the deployment branch."
command -v rsync >/dev/null 2>&1 || err "Local rsync not found. Install rsync and retry."
git rev-parse --show-toplevel >/dev/null 2>&1 || err "Run deploy.sh from a Git checkout."

log "Fetching origin/$DEPLOY_BRANCH..."
git fetch --no-tags origin "refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH" \
  || err "Could not fetch origin/$DEPLOY_BRANCH; nothing was deployed."
DEPLOY_COMMIT="$(git rev-parse --verify "refs/remotes/origin/$DEPLOY_BRANCH^{commit}")"

# Archive the exact fetched commit: no branch switching or local edits in the payload.
SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dockflare-deploy.XXXXXX")"
cleanup() {
  [ -z "$SOURCE_DIR" ] || rm -rf -- "$SOURCE_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git archive "$DEPLOY_COMMIT" | tar -x -C "$SOURCE_DIR"
[ -f "$SOURCE_DIR/docker/compose.yml" ] || err "Selected branch has no docker/compose.yml."

if [ ! -f "$SOURCE_DIR/frontend/package-lock.json" ]; then
  command -v npm >/dev/null 2>&1 || err "npm is required: the selected branch has no frontend/package-lock.json."
  log "Generating the frontend lockfile from origin/$DEPLOY_BRANCH..."
  (cd "$SOURCE_DIR/frontend" && npm install --package-lock-only --ignore-scripts --no-audit --no-fund) \
    || err "Could not prepare the frontend lockfile; nothing was deployed."
fi

ssh -o ConnectTimeout=5 -o BatchMode=yes "$DEPLOY_HOST" "echo ok" >/dev/null 2>&1 \
  || err "Cannot SSH into $DEPLOY_HOST (key auth, host reachable?)."
log "SSH OK."
resolve_default_app_dir

# ---- Confirmation -------------------------------------------------------
echo ""
echo "  Deploying Dockflare"
echo "  Branch: origin/$DEPLOY_BRANCH"
echo "  Commit: $DEPLOY_COMMIT"
echo "  Host: $DEPLOY_HOST:$DEPLOY_APP_DIR"
[ -n "$COMPONENT" ] && echo "  Component: $COMPONENT (rebuild only this service)"
echo ""
REPLY=""
read -r -p "  Continue? [Y/n] " -n 1 REPLY || { log "Aborted: no confirmation input."; exit 1; }
echo ""
[[ $REPLY =~ ^[Nn]$ ]] && { log "Aborted."; exit 0; }

ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_APP_DIR'" \
  || err "Cannot create app dir $DEPLOY_APP_DIR on $DEPLOY_HOST."
ensure_rsync

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
  --exclude='.https-tunnel.env' \
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
  --include='frontend/components.json' \
  --include='frontend/postcss.config.cjs' \
  --include='frontend/tailwind.config.cjs' \
  --include='frontend/tsconfig*.json' \
  --include='frontend/vite.config.ts' \
  --include='frontend/index.html' \
  --include='frontend/src/***' \
  --include='frontend/public/***' \
  --include='docker/***' \
  --include='pyproject.toml' \
  --include='README.md' \
  --exclude='*' \
  "$SOURCE_DIR/" "$DEPLOY_HOST:$DEPLOY_APP_DIR/"
log "Rsync done (origin/$DEPLOY_BRANCH at $DEPLOY_COMMIT)."

# ---- Ship .env separately ----------------------------------------------
if ssh "$DEPLOY_HOST" "test -f '$DEPLOY_APP_DIR/.env'"; then
  log "Preserving existing server .env."
else
  scp "$SCRIPT_DIR/.env" "$DEPLOY_HOST:$DEPLOY_APP_DIR/.env.bootstrap"
  ssh "$DEPLOY_HOST" "umask 077; if [ ! -f '$DEPLOY_APP_DIR/.env' ]; then
    if ! grep -q '^TM_MASTER_KEY_HEX=' '$DEPLOY_APP_DIR/.env.bootstrap'; then
      printf '\\nTM_MASTER_KEY_HEX=' >> '$DEPLOY_APP_DIR/.env.bootstrap'
      openssl rand -hex 32 >> '$DEPLOY_APP_DIR/.env.bootstrap'
    fi
    chmod 600 '$DEPLOY_APP_DIR/.env.bootstrap'
    mv '$DEPLOY_APP_DIR/.env.bootstrap' '$DEPLOY_APP_DIR/.env'
  fi"
fi

if [ "$MODE" = "sync-only" ]; then
  log "Sync-only — skipping build."
  exit 0
fi

# ---- Remote build + up -------------------------------------------------
log "Remote build & up..."
ssh "$DEPLOY_HOST" bash -s "$DEPLOY_APP_DIR" "${COMPONENT:-all}" "${DEPLOY_HOST#*@}" "$DEPLOY_COMMIT" << 'REMOTE_SCRIPT'
  set -euo pipefail
  APP_DIR="$1"; COMPONENT="$2"; SERVER_IP="$3"; COMMIT="$4"
  cd "$APP_DIR"
  chmod 600 .env
  COMPOSE="docker compose --env-file .env -f docker/compose.yml"

  if [ "$COMPONENT" != "all" ]; then
    $COMPOSE build "$COMPONENT"
    $COMPOSE up -d --wait --wait-timeout 120 "$COMPONENT"
  else
    $COMPOSE build backend frontend
    $COMPOSE up -d --wait --wait-timeout 120 backend frontend
  fi

  HOSTNAME="$($COMPOSE exec -T backend python -m app.deploy_https "$SERVER_IP")"
  $COMPOSE cp backend:/app/data/.https-tunnel.env .https-tunnel.env
  chmod 600 .https-tunnel.env
  $COMPOSE --profile https up -d cloudflared

  # Verify TLS, authentication and the public API before reporting success.
  $COMPOSE exec -T backend python - "$HOSTNAME" <<'VERIFY_HTTPS'
import sys
import time
import httpx
from app.config import Settings

hostname = sys.argv[1]
settings = Settings()
with httpx.Client(timeout=10) as client:
    for attempt in range(30):
        try:
            base = "https://" + hostname
            assert client.get(base).status_code == 401
            auth = (settings.basic_auth_user, settings.basic_auth_password)
            assert client.get(base, auth=auth).status_code == 200
            assert client.get(base + "/api/v1/health", auth=auth).json() == {"status": "ok"}
            redirect = client.get("http://" + hostname)
            assert redirect.status_code in (301, 302, 307, 308)
            assert redirect.headers["location"].startswith(base)
            break
        except (httpx.HTTPError, AssertionError, ValueError):
            if attempt == 29:
                raise
            time.sleep(2)
print("  UI: https://" + hostname)
VERIFY_HTTPS
  printf '%s\n' "$COMMIT" > .deployed-revision
  $COMPOSE --profile https ps
REMOTE_SCRIPT

log "Deployment done (origin/$DEPLOY_BRANCH at $DEPLOY_COMMIT)."
