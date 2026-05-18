# Dockflare

**A self-hosted management plane for Cloudflare Tunnels.**

One dashboard to manage all your tunnels, routes, and cloudflared sidecars across Docker Compose projects — replacing scattered `.env` tokens and per-project cloudflared services.

![Dockflare Dashboard](docs/images/demo.png)

## Why

If you run multiple projects on a VPS with Cloudflare Tunnels, you probably have this:

```
project-a/
  docker-compose.yml    # has a cloudflared service
  .env                  # has TUNNEL_TOKEN=eyJ...

project-b/
  docker-compose.yml    # has another cloudflared service
  .env                  # has another TUNNEL_TOKEN=eyJ...

project-c/
  ...same pattern...
```

Every new hostname means editing ingress YAML, every new project means creating a tunnel in the CF dashboard, copying tokens, and restarting stacks. Token rotation? Manual across every project.

**Dockflare replaces all of that:**

- One UI to see all tunnels, routes, containers, and their connections
- Create, edit, delete, and recreate tunnels without touching compose files
- Edit ingress routes with a service picker that shows your running Docker containers
- Export/import tunnel configs for backup and migration
- One-click recreate (token rotation) — same name, same routes, fresh token
- Filter tunnels by machine (IP + architecture) when managing multiple servers
- Remove `cloudflared` from your compose files — Dockflare manages sidecars for you

## Quick Start

### Prerequisites

- A VPS/server running Docker with your projects
- A Cloudflare account with at least one zone

Install the system packages `setup.sh` expects (one-time, requires sudo):

```bash
# make, curl, Python 3.12, Docker
sudo apt update
sudo apt install -y make curl python3.12 python3.12-venv docker.io

# Node.js 20+ (Ubuntu's default 'nodejs' is too old)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

`uv` is *not* a prerequisite — `setup.sh` installs it for you under `~/.local/bin`.

### 1. Create a Cloudflare API Token

Go to [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens) and create a **Custom Token** with these permissions:

| Scope | Resource | Access |
|---|---|---|
| Cloudflare Tunnel | Account | Edit |
| DNS | Zone | Edit |
| Zone | Zone | Read |
| Zone Settings | Zone | Read |
| Account Settings | Account | Read |

Set **Account Resources** to your account and **Zone Resources** to "All zones" (or specific zones).

### 2. Clone and Configure

```bash
git clone https://github.com/yourusername/dockflare.git
cd dockflare
```

Create a `.env` file in the project root:

```
CF_TOKEN=your_cloudflare_api_token_here
DOCKFLARE_BASIC_AUTH_USER=admin
DOCKFLARE_BASIC_AUTH_PASSWORD=change_me_to_a_long_random_password
```

The Docker deployment serves the frontend and API through nginx with HTTP Basic Auth.
Only localhost, private RFC1918 networks, `98.40.139.139`, and `2601:2c3:c37e:7c50:145e:92ce:290a:1ba2` are allowed through the reverse proxy.

### 3. Run setup

```bash
./scripts/setup.sh
# or, equivalently:
make setup
```

The script is idempotent (safe to re-run) and performs:

1. **Install `uv`** to `~/.local/bin` via the official installer if missing.
2. **Verify `node`/`npm`** are on `PATH`; aborts with an install hint if not.
3. **Create `.venv`** with Python 3.12 (`uv venv`) and install Python deps editably with dev extras (`uv pip install -e ".[dev]"`).
4. **`npm install`** inside `frontend/`.
5. **`alembic upgrade head`** to initialize the SQLite DB.
6. **Add your user to the `docker` group** via `sudo usermod -aG docker` if not already a member — required so the manager can read `/var/run/docker.sock`.
7. **Start uvicorn** on `0.0.0.0:8088` with auto-reload. If the docker group was just added, uvicorn is launched under `sg docker` so it inherits the new group without you logging out.

Flags: `--no-start` (install only), `--no-frontend`, `--no-migrate`.

If the docker group was added on this run, your *existing* shells still won't see it until you log out and back in. Until then, run new commands inside `newgrp docker` or just re-run `./scripts/setup.sh`.

### 4. Start the frontend

In a second terminal:

```bash
cd frontend
npm run dev
```

Open **http://your-server-ip:5173** in your browser.

### 5. What You'll See

The dashboard shows:

- **All your CF tunnels** with connection status, origin IP, and machine identifier
- **All Docker Compose projects** with their containers, services, ports, and networks
- **Route mappings** — which hostname points to which container/service
- **Machine filter** — filter tunnels by server (distinguishes by IP + architecture)

### 6. Managing Tunnels

**Adopting existing tunnels:** Your current tunnels appear automatically. To have Dockflare manage a tunnel's sidecar, remove `cloudflared` from that project's `docker-compose.yml` and use the "Recreate" button — Dockflare will spawn and manage the sidecar.

**Creating new tunnels:** Click "New Tunnel", specify a name and optionally a target compose project/service.

**Editing routes:** Click "Edit" on a tunnel to modify its ingress rules. The service picker dropdown shows all running Docker containers with their ports.

**Token rotation:** Click "Recreate" — this exports the config, deletes the old tunnel, creates a new one with the same routes, updates DNS, and spawns a new sidecar. Brief downtime during the switch.

**Backup/migration:** "Export" downloads a JSON config. "Import" creates a new tunnel from that config. Use this to move tunnels between servers.

## Architecture

```
Browser  -->  Vite (dev) / Static (prod)  -->  FastAPI Backend
                                                   |
                                    +--------------+--------------+
                                    |              |              |
                              Docker API    Cloudflare API    SQLite DB
                              (containers)  (tunnels/DNS)    (state)
```

- **Backend:** Python 3.12, FastAPI, SQLModel, httpx, docker-py
- **Frontend:** React 18, TypeScript, TanStack Query, Tailwind CSS, Vite
- **Database:** SQLite (WAL mode) for tunnel state, audit logs, and caching

## Docker Compose Standard

When Dockflare manages your tunnels, your compose files simplify to just the project services:

```yaml
# Before
services:
  web:
    image: my-app
  cloudflared:              # <-- remove this
    image: cloudflare/cloudflared
    command: tunnel run
    environment:
      TUNNEL_TOKEN: ${CF_TUNNEL_TOKEN}

# After
services:
  web:
    image: my-app
  # cloudflared managed by Dockflare
```

Let Docker Compose create the default network automatically. Dockflare discovers the network and attaches the cloudflared sidecar to it.

## Development

```bash
# Run tests
make test-backend

# Lint
make lint-backend

# Type check
make typecheck-backend

# Format
make format
```

## License

MIT
