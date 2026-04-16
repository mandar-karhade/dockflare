# 13 — Testing Strategy

## Test Layers

| Layer | What | Tools | Speed | Count |
|-------|------|-------|-------|-------|
| Unit | Pure logic, helpers, formatters, validators | pytest, vitest | ms | ~70% of total |
| Integration | Service + fake client, DB + real schema | pytest + fakes + temp SQLite | 10-100ms | ~25% |
| Component | React components in isolation | Vitest + RTL | ms | ~30 components |
| Contract | Real CF API replayed from cassettes | pytest + vcrpy | 100ms | per endpoint |
| System | Real Docker via testcontainers | pytest + testcontainers-python | 2-10s | ~10 scenarios |
| E2E | Full stack, browser-driven | Playwright | 10-30s | ~15 flows |

## Unit Tests (Backend)

### What Goes Here
- `VaultService.encrypt/decrypt`
- `priority_calculator.calculate(hostname, path_regex)`
- `ingress_builder.build(routes)`
- `conflict_detector.classify(existing_record, comment)`
- Hostname validation
- Zone resolution algorithm (against a fake zone list)
- Label/comment parsing helpers
- Redaction utility
- Rotation policy → next_due calculator
- DNS operation audit serialization

### Patterns

```python
# tests/unit/services/test_priority_calculator.py
import pytest
from app.services.priority_calculator import calculate_priority

@pytest.mark.parametrize("hostname,path_regex,expected_range", [
    ("app.example.com", None, (780, 820)),       # exact, short
    ("*.example.com", None, (980, 1020)),         # wildcard
    ("api.v2.example.com", "^/v2/.*", (270, 320)), # path regex ranks higher
    ("example.com", None, (790, 810)),             # apex
])
def test_priority_calculation(hostname, path_regex, expected_range):
    result = calculate_priority(hostname, path_regex)
    assert expected_range[0] <= result <= expected_range[1]


def test_more_specific_hostname_gets_lower_priority():
    """Longer hostnames match before shorter ones."""
    specific = calculate_priority("api.v2.staging.example.com", None)
    general = calculate_priority("example.com", None)
    assert specific < general
```

### Vault Tests

```python
def test_vault_round_trip(vault: VaultService):
    token = "eyJhbGciOi..."
    encrypted = vault.encrypt(token)
    decrypted = vault.decrypt(encrypted)
    assert decrypted == token

def test_vault_nonce_uniqueness(vault: VaultService):
    """Each encryption produces different ciphertext."""
    token = "same-plaintext"
    a = vault.encrypt(token)
    b = vault.encrypt(token)
    assert a != b
    assert vault.decrypt(a) == vault.decrypt(b) == token

def test_vault_tamper_detection(vault: VaultService):
    encrypted = vault.encrypt("secret")
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 0x01])
    with pytest.raises(InvalidTagError):
        vault.decrypt(tampered)

def test_vault_rejects_wrong_key_size():
    with pytest.raises(ValueError, match="32 bytes"):
        VaultService(key=b"tooshort")
```

### Redaction Tests

```python
@pytest.mark.parametrize("input_msg,expected_contains,expected_not_contains", [
    ("Token: eyJhbGciOiJIUzI1NiJ9.aGVsbG8.abc",
     ["•••abc"],
     ["eyJhbGciOiJIUzI1NiJ9"]),
    ("Authorization: Bearer sk_live_abc123def456",
     ["Bearer •••456"],
     ["sk_live_abc123"]),
    ("nothing sensitive here", ["nothing sensitive here"], []),
])
def test_redaction_patterns(input_msg, expected_contains, expected_not_contains):
    out = redact(input_msg)
    for s in expected_contains:
        assert s in out
    for s in expected_not_contains:
        assert s not in out
```

## Integration Tests

Cover service-layer logic with fake clients and a real (temp) SQLite DB.

### Fixtures

```python
# tests/conftest.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.db import Base
from app.core.vault import VaultService
from app.clients.cloudflare.fake import FakeCloudflareClient
from app.clients.docker.fake import FakeDockerClient

@pytest_asyncio.fixture
async def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(db_engine):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with AsyncSession(db_engine) as session:
        yield session

@pytest.fixture
def vault():
    return VaultService(key=b"\x00" * 32)

@pytest.fixture
def fake_cf():
    client = FakeCloudflareClient()
    # Seed with one account, two zones
    client.seed_account("acc-1", "Test Account")
    client.seed_zone("zone-1", "example.com", "acc-1")
    client.seed_zone("zone-2", "test.dev", "acc-1")
    return client

@pytest.fixture
def fake_docker():
    client = FakeDockerClient()
    # Seed with one compose project
    client.seed_container(
        id="abc123", name="myapp_web_1",
        labels={"com.docker.compose.project": "myapp",
                "com.docker.compose.service": "web"},
        exposed_ports=[3000],
        networks=["myapp_default"],
        status="running"
    )
    return client
```

### Example: Route Service Integration Test

```python
@pytest.mark.asyncio
async def test_create_route_happy_path(
    db_session, vault, fake_cf, fake_docker
):
    # Arrange: a tunnel exists in DB
    tunnel = await seed_tunnel(db_session, vault, fake_cf, name="myapp")
    
    service = RouteService(
        db=db_session, vault=vault, cf=fake_cf, docker=fake_docker,
        ingress_builder=IngressBuilder(),
    )
    
    # Act
    route = await service.create(CreateRouteInput(
        tunnel_id=tunnel.id,
        hostname="app.example.com",
        target_compose_project="myapp",
        target_compose_service="web",
        target_scheme="http",
        target_port=3000,
    ))
    
    # Assert DB state
    assert route.status == "active"
    assert route.cf_dns_record_id is not None
    assert route.zone_id == "zone-1"
    
    # Assert CF state
    cf_records = fake_cf.list_dns_records(zone_id="zone-1")
    assert any(r["name"] == "app.example.com" and r["proxied"] for r in cf_records)
    
    cf_config = fake_cf.get_tunnel_config("acc-1", tunnel.cf_tunnel_id)
    ingress = cf_config["config"]["ingress"]
    assert any(r.get("hostname") == "app.example.com" for r in ingress)
    assert ingress[-1] == {"service": "http_status:404"}  # catch-all
    
    # Assert Docker state
    sidecars = fake_docker.find_by_label("tunnel-manager.tunnel.id", str(tunnel.id))
    assert len(sidecars) == 1
    assert "myapp_default" in sidecars[0]["networks"]


@pytest.mark.asyncio
async def test_create_route_with_external_dns_conflict(
    db_session, vault, fake_cf, fake_docker
):
    # Arrange: an external A record exists for the hostname
    fake_cf.seed_dns_record(
        zone_id="zone-1",
        name="app.example.com",
        type="A",
        content="192.0.2.1",
        comment=None,  # not ours
    )
    
    tunnel = await seed_tunnel(db_session, vault, fake_cf, name="myapp")
    service = RouteService(...)
    
    # Act + Assert: first attempt returns conflict
    with pytest.raises(DNSConflictError) as exc_info:
        await service.create(CreateRouteInput(
            tunnel_id=tunnel.id,
            hostname="app.example.com",
            target_compose_project="myapp",
            target_compose_service="web",
            target_scheme="http",
            target_port=3000,
        ))
    
    assert exc_info.value.conflict_type == "conflict_external_a"
    
    # Retry with explicit resolution
    route = await service.create(CreateRouteInput(
        tunnel_id=tunnel.id,
        hostname="app.example.com",
        target_compose_project="myapp",
        target_compose_service="web",
        target_scheme="http",
        target_port=3000,
        conflict_resolution="replace_with_backup",
    ))
    
    # Assert backup was created
    backups = db_session.query(DNSBackup).filter_by(hostname="app.example.com").all()
    assert len(backups) == 1
    assert backups[0].record_type == "A"
    assert backups[0].record_content == "192.0.2.1"
    
    # Assert record is now ours
    cf_records = fake_cf.list_dns_records(zone_id="zone-1")
    ours = [r for r in cf_records if r["name"] == "app.example.com"]
    assert len(ours) == 1
    assert ours[0]["type"] == "CNAME"
    assert ours[0]["content"].endswith(".cfargotunnel.com")
    assert ours[0]["comment"].startswith("tunnel-manager:")
```

### Rotation Integration Test

```python
@pytest.mark.asyncio
async def test_soft_rotation_with_rolling_restart(
    db_session, vault, fake_cf, fake_docker
):
    tunnel = await seed_tunnel_with_sidecar(db_session, vault, fake_cf, fake_docker)
    old_sidecar_id = tunnel.cloudflared_container_id
    old_token = vault.decrypt(tunnel.token_encrypted)
    
    # Simulate CF returning a new token
    fake_cf.rotate_tunnel_token(tunnel.cf_tunnel_id)
    
    service = RotationService(...)
    event = await service.rotate_tunnel_soft(tunnel.id, triggered_by="manual")
    
    assert event.status == "success"
    assert event.downtime_seconds < 5.0  # rolling should be fast
    
    # Old sidecar gone
    with pytest.raises(NotFound):
        fake_docker.get_container(old_sidecar_id)
    
    # New sidecar running with new token
    db_session.refresh(tunnel)
    new_sidecar = fake_docker.get_container(tunnel.cloudflared_container_id)
    assert new_sidecar["status"] == "running"
    assert new_sidecar["env"].get("TUNNEL_TOKEN") != old_token
    
    # Event recorded
    events = db_session.query(RotationEvent).filter_by(entity_id=tunnel.id).all()
    assert len(events) == 1
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_rotation_failure_keeps_old_sidecar_running(
    db_session, vault, fake_cf, fake_docker
):
    tunnel = await seed_tunnel_with_sidecar(db_session, vault, fake_cf, fake_docker)
    old_sidecar_id = tunnel.cloudflared_container_id
    
    # Simulate: new sidecar never reaches healthy state
    fake_cf.force_tunnel_connections_empty(tunnel.cf_tunnel_id)
    
    service = RotationService(...)
    with pytest.raises(RotationError):
        await service.rotate_tunnel_soft(tunnel.id, triggered_by="manual")
    
    # Old sidecar still running
    old = fake_docker.get_container(old_sidecar_id)
    assert old["status"] == "running"
    
    # Event recorded as failed
    events = db_session.query(RotationEvent).filter_by(entity_id=tunnel.id).all()
    assert events[-1].status == "failed"
```

## Contract Tests (CF API)

Use `vcrpy` to record real CF responses once, replay forever.

```python
import vcr

my_vcr = vcr.VCR(
    cassette_library_dir="tests/fixtures/cf_responses",
    record_mode="once",
    filter_headers=["Authorization", "X-Auth-Email", "X-Auth-Key"],
    filter_query_parameters=["token"],
)

@my_vcr.use_cassette("list_tunnels.yaml")
@pytest.mark.asyncio
async def test_list_tunnels_matches_documented_shape():
    client = CloudflareClient(token=os.environ.get("CF_TEST_TOKEN", "redacted"))
    result = await client.list_tunnels(account_id="acc-1")
    assert isinstance(result, list)
    for t in result:
        assert "id" in t
        assert "name" in t
        assert "created_at" in t
```

Record mode `once` means:
- If cassette exists, use it (offline, fast)
- If cassette missing, record from real API (requires real token)
- In CI: cassettes always present, no network needed

## System Tests (Testcontainers)

Real Docker daemon via testcontainers-python. Use sparingly; slow.

```python
from testcontainers.compose import DockerCompose

@pytest.fixture(scope="module")
def docker_stack():
    with DockerCompose("tests/fixtures/sample_stack") as compose:
        compose.wait_for("http://localhost:3000")
        yield compose

@pytest.mark.system
def test_can_spawn_cloudflared_sidecar_and_reach_target(docker_stack):
    docker_client = docker.from_env()
    
    # Find the target container
    target = docker_client.containers.list(
        filters={"label": "com.docker.compose.service=web"}
    )[0]
    target_network = list(target.attrs["NetworkSettings"]["Networks"].keys())[0]
    
    # Spawn a dummy cloudflared-like container that tries to reach target
    sidecar = docker_client.containers.run(
        image="curlimages/curl:latest",
        command=["curl", "-sS", "http://web:3000/health"],
        network=target_network,
        detach=False,
        remove=True,
    )
    # If it exited 0 and returned expected content, network reachability works
    assert b"healthy" in sidecar
```

## Component Tests (Frontend)

```typescript
// frontend/tests/unit/components/StatusBadge.test.tsx
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/common/StatusBadge";

describe("StatusBadge", () => {
  it("renders active status with green styling", () => {
    render(<StatusBadge status="active" />);
    const el = screen.getByText("active");
    expect(el).toHaveClass("bg-green-500");
  });
  
  it("renders error status with red styling and icon", () => {
    render(<StatusBadge status="error" detail="Connection refused" />);
    expect(screen.getByText("error")).toHaveClass("bg-red-500");
    expect(screen.getByLabelText("error icon")).toBeInTheDocument();
    expect(screen.getByTitle("Connection refused")).toBeInTheDocument();
  });
});
```

For components that use TanStack Query, wrap in a test-scoped provider:

```typescript
function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
  );
}
```

Mock API using MSW:

```typescript
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";

const server = setupServer(
  http.get("/api/v1/tunnels", () => {
    return HttpResponse.json({
      items: [{ id: 1, cf_tunnel_name: "test", status: "active", routes_count: 2 }],
      total: 1,
    });
  })
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

## E2E Tests (Playwright)

```typescript
// frontend/tests/e2e/bootstrap.spec.ts
import { test, expect } from "@playwright/test";

test("first-run bootstrap creates working setup", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/bootstrap/);
  
  // Step 1: Welcome
  await page.getByRole("button", { name: "Get Started" }).click();
  
  // Step 2: Token
  await page.getByLabel("CF API Token").fill(process.env.CF_TEST_TOKEN!);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText(/Token verified/)).toBeVisible();
  
  // Step 3: Account
  await page.getByRole("radio", { name: /Test Account/ }).check();
  await page.getByRole("button", { name: "Continue" }).click();
  
  // Step 4: Fresh start
  await page.getByRole("button", { name: "Start fresh" }).click();
  
  // Step 5: Complete
  await page.getByRole("button", { name: "Complete Setup" }).click();
  
  // Should land on dashboard
  await expect(page).toHaveURL("/");
  await expect(page.getByText("Dashboard")).toBeVisible();
  await expect(page.getByText(/0 tunnels/)).toBeVisible();
});


test("create tunnel and route, verify public URL responds", async ({ page, request }) => {
  // (assumes bootstrap already done via API)
  await page.goto("/tunnels");
  await page.getByRole("button", { name: "New Tunnel" }).click();
  await page.getByLabel("Name").fill("e2e-test-tunnel");
  await page.getByLabel("Primary Project").fill("e2e-demo");
  await page.getByLabel("Primary Service").fill("web");
  await page.getByRole("button", { name: "Create" }).click();
  
  await expect(page.getByText("e2e-test-tunnel")).toBeVisible({ timeout: 10_000 });
  
  // Add a route
  await page.getByText("e2e-test-tunnel").click();
  await page.getByRole("button", { name: "Add Route" }).click();
  await page.getByLabel("Hostname").fill("e2e.test.example.com");
  await page.getByLabel("Target Port").fill("3000");
  await page.getByRole("button", { name: "Create" }).click();
  
  // Wait for status "active"
  await expect(page.getByText("active")).toBeVisible({ timeout: 30_000 });
  
  // Hit the public URL from outside
  const response = await request.get("https://e2e.test.example.com/", {
    ignoreHTTPSErrors: false,
  });
  expect(response.status()).toBe(200);
});
```

## Coverage Goals

- Unit: ≥90% line coverage on `app/core`, `app/services`, `app/utils`
- Integration: every API endpoint has at least one happy path + one error path test
- E2E: every major flow in [09-frontend-spec.md](09-frontend-spec.md)

Not tracked: `app/main.py` (framework glue), migration files, `__init__.py`.

## CI Pipeline

```yaml
# .github/workflows/ci.yml (sketch)
name: CI
on: [push, pull_request]

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --dev
      - run: uv run ruff format --check
      - run: uv run ruff check
      - run: uv run mypy app
      - run: uv run pytest tests/unit tests/integration -v --cov=app
      - uses: codecov/codecov-action@v4

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm }
      - run: pnpm install --frozen-lockfile
        working-directory: frontend
      - run: pnpm lint
        working-directory: frontend
      - run: pnpm typecheck
        working-directory: frontend
      - run: pnpm test
        working-directory: frontend
      - run: pnpm build
        working-directory: frontend

  e2e:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker/compose.dev.yml up -d
      - run: npx playwright install --with-deps
      - run: npx playwright test
        env:
          CF_TEST_TOKEN: ${{ secrets.CF_TEST_TOKEN }}

  image:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v5
        with:
          tags: tunnel-manager:${{ github.sha }}
          load: true
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: tunnel-manager:${{ github.sha }}
          severity: CRITICAL,HIGH
          exit-code: 1
```

## Test Data Management

- **Fake clients** carry seeded state configured per-test.
- **VCR cassettes** checked into repo under `tests/fixtures/cf_responses/`.
- **DB seeds** as Python factories, not SQL files:

```python
async def seed_tunnel(db, vault, fake_cf, **overrides):
    cf_tunnel = await fake_cf.create_tunnel("acc-1", overrides.get("name", "test"))
    token = await fake_cf.get_tunnel_token("acc-1", cf_tunnel["id"])
    tunnel = Tunnel(
        cf_tunnel_id=cf_tunnel["id"],
        cf_tunnel_name=cf_tunnel["name"],
        account_id="acc-1",
        token_encrypted=vault.encrypt(token),
        token_last_four=token[-4:],
        token_fetched_at=datetime.utcnow(),
        **overrides,
    )
    db.add(tunnel)
    await db.commit()
    await db.refresh(tunnel)
    return tunnel
```

## Performance Tests

Ad-hoc via `pytest-benchmark`:

```python
@pytest.mark.benchmark
async def test_drift_scan_100_tunnels(fake_cf, fake_docker, db_seeded_100_tunnels):
    service = ReconciliationService(...)
    start = time.monotonic()
    scan_id = await service.run_drift_scan()
    duration = time.monotonic() - start
    assert duration < 30.0  # must complete in <30s for 100 tunnels
```

Run on demand, not every commit.

## Mutation Testing

Consider `mutmut` or `cosmic-ray` on critical modules (vault, conflict detector). Not blocking CI; run quarterly.
