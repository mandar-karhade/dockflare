# 08 — Security

## Threat Model

### Assets to Protect

1. **Cloudflare API token** — enables tunnel, DNS, and zone operations across the account
2. **Tunnel tokens** — each grants ability to connect as a specific tunnel and receive traffic
3. **Master encryption key** — decrypts all other secrets
4. **Docker daemon access** — via the socket proxy; enables container spawn/modify
5. **User session cookies** — authenticate GUI users
6. **DNS records** — misuse could redirect traffic

### Threat Actors

1. **External attacker with network access** — scans ports, fuzzes endpoints, attempts injection
2. **Attacker with GUI credential** — phished password, credential stuffing
3. **Attacker with container-level access** — exploited vulnerability in a different container on the VPS
4. **Attacker with host-level access** — already root on the VPS (game over for most systems)
5. **Malicious CF API response** — prompt injection-style content in record names, comments, etc.
6. **Supply chain compromise** — poisoned image of cloudflared, tecnativa proxy, or Python dep

### Non-Threats (Out of Scope)

- Full root-on-host compromise (nothing meaningfully defends against this)
- Side-channel attacks on encryption (SQLite data doesn't justify this defense depth)
- Nation-state adversary with global passive capability

## Defense Layers

### Layer 1: Network Isolation

- Manager bound to `127.0.0.1` only by default, or behind a reverse proxy with auth
- Manager's own management UI accessed via its own CF tunnel with CF Access (recommended)
- Manager container on isolated `tm-internal` Docker network; does not join project networks
- Only socket-proxy has `/var/run/docker.sock` mounted, read-only
- All outbound traffic to CF API goes over HTTPS with cert verification

### Layer 2: Socket Proxy Filtering

`tecnativa/docker-socket-proxy` whitelists only the Docker API paths the manager needs:

| Endpoint Class | Permitted | Reason |
|----------------|-----------|--------|
| `/containers/*` | GET, POST | Read + create/modify cloudflared sidecars |
| `/networks/*` | GET, POST | Attach sidecars to target networks |
| `/images/*` | GET | Verify cloudflared image exists |
| `/events` | GET | Stream lifecycle events |
| `/version`, `/info`, `/_ping` | GET | Health checks |
| Everything else | Denied | Defense in depth |

Notably blocked: `AUTH`, `BUILD`, `COMMIT`, `EXEC` (critical — no shell execution), `VOLUMES`, `SECRETS`.

### Layer 3: Secret Encryption at Rest

All sensitive DB columns encrypted with AES-256-GCM:

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class VaultService:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("Key must be 32 bytes")
        self._key = key
        self._cipher = AESGCM(key)
    
    def encrypt(self, plaintext: str) -> bytes:
        plaintext_bytes = plaintext.encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext_bytes, None)
        return nonce + ciphertext  # 12-byte nonce + ciphertext + 16-byte tag
    
    def decrypt(self, encrypted: bytes) -> str:
        if len(encrypted) < 28:  # 12 nonce + 16 tag minimum
            raise ValueError("Invalid encrypted blob")
        nonce = encrypted[:12]
        ciphertext = encrypted[12:]
        plaintext = self._cipher.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
```

Properties:
- Authenticated encryption; tampering causes decryption to fail loudly
- Random 96-bit nonce per encryption (GCM collision risk is negligible at our scale)
- Key stored in Docker secret, not environment variable (env vars leak via `ps`, `/proc`)
- Key is 32 random bytes from a secure source (not derived from user password)

### Layer 4: Master Key Management

Master key loaded from `/run/secrets/master_key` on startup:

```python
def load_master_key() -> bytes:
    key_path = os.environ.get("MASTER_KEY_FILE", "/run/secrets/master_key")
    with open(key_path, "rb") as f:
        key = f.read().strip()
    if len(key) != 32:
        # If hex-encoded
        if len(key) == 64:
            key = bytes.fromhex(key.decode())
        elif len(key) == 44:  # base64
            key = base64.b64decode(key)
        else:
            raise ValueError(f"Invalid master key length: {len(key)}")
    return key
```

Generation for first-time deploy:
```bash
openssl rand -hex 32 > ./secrets/master_key
chmod 400 ./secrets/master_key
```

Backup requirement: user must backup this file somewhere safe. Without it, the encrypted DB is unreadable. Manager surfaces this prominently during bootstrap.

### Layer 5: Token Handling Rules

Runtime rules enforced throughout the codebase:

1. Never log raw tokens — log only last 4 chars
2. Never include tokens in error messages or stack traces
3. Never write tokens to temp files
4. Never pass tokens via command-line args except directly to cloudflared (where it's unavoidable; see note below)
5. Mask `Authorization` headers in all HTTP logs
6. Zero out decrypted token buffers after use (best effort in Python)

Cloudflared caveat: `cloudflared tunnel run --token ...` puts the token on the cmdline, visible in `ps`. This is a Cloudflare design choice. Mitigation: the sidecar runs in its own PID namespace, so other containers on the host can't see each other's cmdlines by default. Verify with `docker inspect` that PID mode isn't "host".

Alternative: write token to tmpfs file, pass `--token-file` — but cloudflared doesn't support this flag. Use env var instead: `-e TUNNEL_TOKEN=...` reduces exposure:

```python
# Prefer env var over CLI arg
docker.containers.run(
    image="cloudflare/cloudflared:latest",
    command=["tunnel", "--no-autoupdate", "run"],
    environment={"TUNNEL_TOKEN": token},
    # ...
)
```

Token still visible in container env via `docker inspect`, but not in process table. This is better for hosts with multiple admin users.

### Layer 6: Container Hardening

All spawned containers (including the manager itself):

```yaml
read_only: true
cap_drop: ["ALL"]
security_opt:
  - no-new-privileges:true
  - seccomp=default   # or custom profile
tmpfs:
  - /tmp:size=10m,mode=1777  # only if needed
```

User namespace remapping (if available): sidecars run as non-root user even if they escape the container.

### Layer 7: Input Validation

All user input validated via Pydantic:

- Hostname: regex-validated against RFC 1035 rules
- Path regex: compiled once at validation time (rejects malformed)
- Port: int in [1, 65535]
- Compose project/service: limited character set
- API tokens: length + charset validation before any use
- URLs in origin_options: parsed and normalized

SQL injection: not a risk with SQLAlchemy ORM, but raw SQL paths audited.

Command injection: no shell execution anywhere. Docker SDK uses Python API, not CLI.

### Layer 8: Output Sanitization

Responses that include data from CF:
- HTML encoded in GUI (React does this by default)
- Container names sanitized before use in shell contexts (n/a currently, but guard)
- Comment fields stripped of control chars
- Log messages escape user input

### Layer 9: GUI Authentication

V1: single admin user.

- Password hashed with Argon2id
- Session cookies: HTTP-only, Secure (when over HTTPS), SameSite=Strict
- Session tokens: 32 random bytes, stored in `sessions` table with expiration
- CSRF protection: double-submit cookie pattern or SameSite=Strict reliance
- Login rate-limit: 5 attempts per 15 minutes per IP

### Layer 10: Audit Logging

Every state-changing operation logged to `audit_log` with:
- Actor (user ID or "system")
- Request ID (correlates with access logs)
- Before/after JSON snapshots
- Timestamp

Retention: indefinite by default. Manual purge endpoint for compliance.

## Secret Lifecycle

### Creation
- Master key: generated once at deploy time, backed up offline
- CF API token: created by user in CF dashboard, pasted into manager GUI
- Tunnel tokens: created by CF when tunnel is created, fetched via API
- Session tokens: generated on each login

### Storage
- Master key: Docker secret, only readable by manager process
- All other tokens: encrypted with master key, stored in SQLite

### Rotation
- Master key: manual, annual recommended, triggers re-encryption of all secrets
- CF API token: user-initiated via GUI, old token retained 7 days for rollback
- Tunnel tokens: scheduled (configurable) or manual, with rolling restart
- Session tokens: new on each login; old invalidated on logout

### Destruction
- Tunnel tokens: overwritten in memory on rotation, old row deleted from DB
- Sessions: deleted on logout or expiration
- Backups (encrypted): retained per policy, then securely deleted

### Backup
- DB encrypted by Litestream before upload to R2
- Master key MUST be backed up separately (offline, password manager, or printed)
- R2 credentials minimally scoped to one bucket

## Attack Scenarios and Mitigations

### Attacker gains read access to the DB file
- All tokens are encrypted; unusable without master key
- Some metadata leaks (hostnames, target containers, CF account IDs)
- Mitigation depth: encrypt the DB file itself via LUKS or filesystem encryption

### Attacker gains exec access to the manager container
- Can call Docker API via socket-proxy (limited endpoints)
- Can read master key from `/run/secrets/master_key`
- Can decrypt DB and exfiltrate tokens
- Game over for that VPS's tunnel infrastructure
- Mitigation: monitor manager logs for unexpected behavior; run with minimal base image (distroless or alpine); no shell in the image ideally

### Attacker compromises a project container (not the manager)
- Attacker has access to its own Docker network
- Cloudflared sidecars on that network have a tunnel token in their env
- Attacker can exfiltrate that tunnel token
- Impact: can proxy traffic through the tunnel, but can't modify routes/DNS
- Mitigation: don't share networks unnecessarily; per-container tunnel limits blast radius to one project

### Attacker with host root
- Can read everything, including master key and all secrets
- No meaningful defense at manager layer
- Mitigation: host-level hardening, SSH key-only auth, minimal host surface

### Malicious DNS record in CF account
- Scenario: someone with CF access (stolen API token) adds a malicious CNAME
- Manager's drift detection surfaces it
- Since record doesn't have `tunnel-manager:` comment, it's tagged as external
- User sees it in drift findings and can respond

### Prompt injection in CF responses
- CF record comments, names, etc. could contain hostile content if attacker had CF access
- Content is treated as opaque data by manager
- GUI renders as text (React escaping)
- Not used in any shell/exec contexts
- Low risk

## Hardening Checklist

For deployment:

- [ ] Generate master key via `openssl rand -hex 32`
- [ ] Chmod 400 on secret files
- [ ] Use Docker secrets, not env vars, for all sensitive values
- [ ] Back up master key offline
- [ ] Enable Litestream backup to R2 with separate credentials
- [ ] Bind manager UI to localhost, expose via CF tunnel with Access policy
- [ ] Configure CF API token with minimal scopes
- [ ] Enable CF API token IP restriction to VPS IP
- [ ] Set CF API token expiration (optional but recommended)
- [ ] Enable HTTPS on GUI access (via CF tunnel this is automatic)
- [ ] Configure Slack/email webhooks for rotation failures
- [ ] Review socket-proxy env vars (nothing extra enabled)
- [ ] Verify `docker-socket-proxy` is latest tag or pinned
- [ ] Verify `cloudflared` image pinned (avoid blind `:latest`)
- [ ] Disable scheduler bulk rotation without confirmation on first run
- [ ] Set `policy.require_confirmation_for_destructive: true`

## Security Headers (GUI)

Set on all HTTP responses:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'self'; connect-src 'self' wss:; img-src 'self' data:
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: interest-cohort=()
```

FastAPI middleware adds these automatically.

## Dependency Security

- Pin all Python deps via `uv.lock` or `poetry.lock`
- Run `pip-audit` in CI
- Pin Docker base images by digest, not tag
- Use `hadolint` on Dockerfiles
- Use `grype` or similar on built images

## Incident Response

If tokens are suspected compromised:

1. Immediate: revoke CF API token in CF dashboard
2. Within 1 hour: rotate all tunnel tokens via force-recreate
3. Within 1 day: rotate master encryption key
4. Review audit log for unexpected operations
5. Review CF audit log in dashboard for unexpected API calls
6. If VPS compromise suspected: rebuild VPS from scratch, restore from clean backup
