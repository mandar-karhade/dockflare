# 04 — Docker Integration

## Connection Model

Manager does **not** connect directly to `/var/run/docker.sock`. It connects to a `docker-socket-proxy` container over an internal network:

```
Manager → TCP → docker-socket-proxy → Unix socket → Docker daemon
```

Rationale:
- Socket proxy can whitelist specific API endpoints (defense against manager compromise)
- Manager itself never mounts the socket
- If manager image is compromised, attacker gets only the API surface the proxy allows

## Socket Proxy Configuration

```yaml
socket-proxy:
  image: tecnativa/docker-socket-proxy:latest
  environment:
    # Read access
    CONTAINERS: 1
    NETWORKS: 1
    IMAGES: 1
    EVENTS: 1
    VERSION: 1
    INFO: 1
    PING: 1
    # Write access (required for manager functionality)
    POST: 1                 # enables POST/PUT/DELETE
    # Explicitly disabled (belt and suspenders)
    AUTH: 0
    BUILD: 0
    COMMIT: 0
    CONFIGS: 0
    DISTRIBUTION: 0
    EXEC: 0
    GRPC: 0
    NODES: 0
    PLUGINS: 0
    SECRETS: 0
    SERVICES: 0
    SESSION: 0
    SWARM: 0
    SYSTEM: 0
    TASKS: 0
    VOLUMES: 0
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
```

The manager environment includes `DOCKER_HOST=tcp://socket-proxy:2375`.

## Docker SDK Choice

Use `docker` (aka `docker-py`) for Python. Pin version (e.g., `docker==7.1.0`).

Common initialization:

```python
import docker
from docker import DockerClient

def get_docker_client() -> DockerClient:
    return docker.DockerClient(base_url=os.environ["DOCKER_HOST"])
```

Since `docker-py` is synchronous, wrap all calls in `asyncio.to_thread()` when calling from async handlers:

```python
async def list_containers(filters: dict | None = None) -> list[dict]:
    return await asyncio.to_thread(
        lambda: [c.attrs for c in docker_client.containers.list(all=True, filters=filters)]
    )
```

Alternatively, use `aiodocker` if you prefer native async. Recommendation: stick with `docker-py` + `to_thread` — it's better maintained and has richer API surface.

## Key Operations

### List containers by compose project
```python
def find_by_compose(project: str, service: str = None) -> list[Container]:
    filters = {"label": [f"com.docker.compose.project={project}"]}
    if service:
        filters["label"].append(f"com.docker.compose.service={service}")
    return docker_client.containers.list(all=True, filters=filters)
```

### List containers managed by tunnel-manager
```python
def list_managed_sidecars() -> list[Container]:
    return docker_client.containers.list(
        all=True,
        filters={"label": "tunnel-manager.managed=true"}
    )
```

### Get container networks
```python
def get_networks(container: Container) -> list[str]:
    container.reload()  # refresh attrs
    return list(container.attrs["NetworkSettings"]["Networks"].keys())
```

### Get exposed ports from image
```python
def get_exposed_ports(container: Container) -> list[int]:
    container.reload()
    exposed = container.attrs["Config"].get("ExposedPorts") or {}
    # keys are "3000/tcp", "8080/udp", etc.
    ports = []
    for k in exposed.keys():
        port_str, proto = k.split("/")
        if proto == "tcp":  # UDP rarely needed for HTTP tunnels
            ports.append(int(port_str))
    return ports
```

Note: `ExposedPorts` is what the image declares via `EXPOSE`. For ports bound at runtime via `--expose`, they'd be in the same field. For ports published to host (`-p`), those are `NetworkSettings.Ports` — but we explicitly don't care about host port mappings because the manager avoids them entirely.

### Create cloudflared sidecar
```python
def spawn_cloudflared(
    tunnel_id_short: str,
    token: str,
    networks: list[str],
    labels: dict,
    name_prefix: str
) -> Container:
    # Use the first network at creation; attach others post-creation
    primary_network = networks[0]
    
    container = docker_client.containers.run(
        image="cloudflare/cloudflared:latest",
        name=f"{name_prefix}-{tunnel_id_short}",
        command=["tunnel", "--no-autoupdate", "run", "--token", token],
        network=primary_network,
        labels={
            **labels,
            "tunnel-manager.managed": "true",
            "tunnel-manager.version": "1"
        },
        restart_policy={"Name": "unless-stopped"},
        detach=True,
        # Sensible resource limits
        mem_limit="128m",
        memswap_limit="128m",
        # Security
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        # Logging
        log_config={"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        # No tmpfs, no mounts needed for token-based cloudflared
    )
    
    # Attach additional networks
    for net in networks[1:]:
        network_obj = docker_client.networks.get(net)
        network_obj.connect(container)
    
    return container
```

### Attach container to network
```python
def ensure_on_network(container: Container, network_name: str):
    container.reload()
    current = set(container.attrs["NetworkSettings"]["Networks"].keys())
    if network_name in current:
        return  # already attached
    network = docker_client.networks.get(network_name)
    network.connect(container)
```

### Remove sidecar
```python
def remove_sidecar(container: Container, timeout: int = 10):
    try:
        container.stop(timeout=timeout)
    except docker.errors.APIError as e:
        if "is not running" not in str(e):
            raise
    container.remove(force=True)
```

## Container Naming Convention

All manager-spawned containers follow:
```
cftunnel-{project}-{service}[-{suffix}]
```

Examples:
- `cftunnel-ghost-web` — tunnel for ghost project's web service
- `cftunnel-ghost-web-new` — temporary during rolling rotation
- `cftunnel-standalone-myapp` — for non-compose containers (project defaults to "standalone")

## Label Conventions

All manager-spawned containers have:

| Label | Purpose |
|-------|---------|
| `tunnel-manager.managed` | `"true"` — identifies our containers |
| `tunnel-manager.version` | Schema version for future migration |
| `tunnel-manager.tunnel.id` | DB ID of the tunnel row |
| `tunnel-manager.tunnel.cf_id` | CF tunnel UUID |
| `tunnel-manager.target.project` | Primary target compose project |
| `tunnel-manager.target.service` | Primary target compose service |
| `tunnel-manager.created_at` | ISO timestamp |
| `tunnel-manager.rotation.active` | `"true"` during rolling rotation (temporary) |

## Event Stream

Docker events surface lifecycle changes. Subscribe on startup:

```python
async def event_listener():
    """Long-running task that processes Docker events."""
    loop = asyncio.get_event_loop()
    
    def generator():
        return docker_client.events(
            decode=True,
            filters={"type": ["container", "network"]}
        )
    
    # docker-py events() is a blocking generator
    # run in a thread, yield to async queue
    queue: asyncio.Queue = asyncio.Queue()
    
    def thread_fn():
        for event in generator():
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)
    
    thread = threading.Thread(target=thread_fn, daemon=True)
    thread.start()
    
    while True:
        event = await queue.get()
        try:
            await dispatch_event(event)
        except Exception as e:
            log.error(f"Event handler failed: {e}", exc_info=True)
```

### Event Types to Handle

```python
async def dispatch_event(event: dict):
    event_type = event.get("Type")
    action = event.get("Action")
    
    if event_type == "container":
        handlers = {
            "start": on_container_start,
            "die": on_container_die,
            "destroy": on_container_destroy,
            "rename": on_container_rename,
        }
    elif event_type == "network":
        handlers = {
            "connect": on_network_connect,
            "disconnect": on_network_disconnect,
        }
    else:
        return
    
    handler = handlers.get(action)
    if handler:
        await handler(event)
```

### Handler Implementations

```python
async def on_container_start(event: dict):
    container_id = event["id"]
    attrs = event["Actor"]["Attributes"]
    
    # Update cache
    await container_cache_service.upsert(container_id)
    
    # Is this a target of any route?
    project = attrs.get("com.docker.compose.project")
    service = attrs.get("com.docker.compose.service")
    name = attrs.get("name")
    
    matching_routes = []
    if project and service:
        matching_routes = await route_service.find_by_target(project, service)
    if not matching_routes and name:
        matching_routes = await route_service.find_by_container_name(name)
    
    for route in matching_routes:
        # Ensure sidecar is running for this route's tunnel
        await tunnel_service.ensure_sidecar_healthy(route.tunnel_id)
        # Ensure sidecar is attached to this container's network
        await tunnel_service.ensure_sidecar_on_target_network(route.tunnel_id, container_id)
        # Mark route healthy if previously target_down
        if route.status == "target_down":
            await route_service.set_status(route.id, "active", "Target restarted")

async def on_container_die(event: dict):
    container_id = event["id"]
    attrs = event["Actor"]["Attributes"]
    
    # Is this a managed sidecar?
    if attrs.get("tunnel-manager.managed") == "true":
        tunnel_id = int(attrs.get("tunnel-manager.tunnel.id", 0))
        if tunnel_id:
            # Log it; restart policy should handle recovery
            log.warning(f"Managed sidecar {container_id[:12]} died, awaiting restart")
            # If restart policy fails, mark tunnel degraded
            asyncio.create_task(check_sidecar_recovery(tunnel_id, container_id))
        return
    
    # Is this a route target?
    project = attrs.get("com.docker.compose.project")
    service = attrs.get("com.docker.compose.service")
    if project and service:
        routes = await route_service.find_by_target(project, service)
        for route in routes:
            await route_service.set_status(route.id, "target_down", "Target container stopped")

async def on_container_destroy(event: dict):
    container_id = event["id"]
    attrs = event["Actor"]["Attributes"]
    
    # Managed sidecar destroyed (unexpected)
    if attrs.get("tunnel-manager.managed") == "true":
        tunnel_id = int(attrs.get("tunnel-manager.tunnel.id", 0))
        if tunnel_id:
            # Recreate it — but throttle to avoid loops
            await tunnel_service.recreate_sidecar_if_expected(tunnel_id)
        return
    
    # Route target destroyed
    project = attrs.get("com.docker.compose.project")
    service = attrs.get("com.docker.compose.service")
    if project and service:
        routes = await route_service.find_by_target(project, service)
        for route in routes:
            await route_service.set_status(route.id, "orphaned", "Target removed")
    
    # Cleanup cache
    await container_cache_service.delete(container_id)

async def on_network_disconnect(event: dict):
    network_id = event["Actor"]["ID"]
    container_id = event["Actor"]["Attributes"]["container"]
    
    # If a managed sidecar got disconnected from a network it should be on,
    # reconcile
    container = await docker_service.inspect(container_id)
    if container.labels.get("tunnel-manager.managed") == "true":
        tunnel_id = int(container.labels.get("tunnel-manager.tunnel.id", 0))
        await tunnel_service.reconcile_sidecar_networks(tunnel_id)
```

### Event Gap Handling

Events can be missed if the listener is down (manager restart, network blip). On reconnect, do a full reconciliation pass:

```python
async def reconcile_on_startup():
    # Walk DB tunnels, verify each sidecar exists and is running
    for tunnel in db.query_tunnels(status="active"):
        sidecars = find_containers_by_label(
            "tunnel-manager.tunnel.id", str(tunnel.id)
        )
        if not sidecars:
            # Sidecar missing; recreate
            await tunnel_service.create_sidecar(tunnel.id)
        elif len(sidecars) > 1:
            # Multiple sidecars for one tunnel (rotation leftover?); clean up
            await tunnel_service.deduplicate_sidecars(tunnel.id)
        else:
            sidecar = sidecars[0]
            if sidecar.status != "running":
                sidecar.start()
            # Verify networks match what routes need
            await tunnel_service.reconcile_sidecar_networks(tunnel.id)
    
    # Update container_cache for all containers
    await container_cache_service.full_resync()
```

## Network Attachment Strategy

When adding a route to a tunnel, the cloudflared sidecar must be on the same Docker network as the target container. Algorithm:

```python
async def ensure_sidecar_can_reach(tunnel_id: int, target_container_id: str):
    tunnel = db.get_tunnel(tunnel_id)
    sidecar = docker.containers.get(tunnel.cloudflared_container_id)
    target = docker.containers.get(target_container_id)
    
    sidecar_networks = set(sidecar.attrs["NetworkSettings"]["Networks"].keys())
    target_networks = set(target.attrs["NetworkSettings"]["Networks"].keys())
    
    shared = sidecar_networks & target_networks
    if shared:
        return  # already reachable
    
    # Pick the best network to attach to:
    # 1. Prefer non-default bridge networks
    # 2. Prefer networks owned by same compose project as target
    # 3. Fall back to first available
    target_primary = pick_primary_network(target)
    
    docker.networks.get(target_primary).connect(sidecar)
    log.info(f"Attached sidecar {sidecar.short_id} to network {target_primary}")
```

## Image Management

On startup, pull cloudflared image if missing:

```python
async def ensure_cloudflared_image():
    image = "cloudflare/cloudflared:latest"
    try:
        docker.images.get(image)
    except docker.errors.ImageNotFound:
        log.info(f"Pulling {image}...")
        docker.images.pull(image)
```

Also offer "update cloudflared" action in GUI that re-pulls and restarts all sidecars.

## Container Health Checks

cloudflared supports a metrics endpoint. Enable in command:

```python
command=[
    "tunnel",
    "--no-autoupdate",
    "--metrics", "0.0.0.0:2000",
    "run",
    "--token", token
]
```

Then the manager can probe `http://{sidecar_ip}:2000/ready` for local health. Combined with CF `/connections` API for edge-side health, gives a complete picture.

Since the manager isn't on project networks, it can't directly curl the sidecar. Options:
1. Use `docker.api.exec_create` to run curl inside the sidecar's own network namespace (hacky but works)
2. Rely on CF `/connections` API + Docker container status as proxy
3. Add a brief network attachment to poll, then detach (heavy)

Recommendation: go with option 2 initially. Option 1 available as deep-dive health check.

## Graceful Sidecar Shutdown

When removing a route or deleting a tunnel:

```python
async def graceful_stop(container: Container):
    # cloudflared handles SIGTERM cleanly, draining connections
    container.kill(signal="SIGTERM")
    
    # Wait up to 30s for clean exit
    try:
        exit_code = container.wait(timeout=30)
        log.info(f"Clean exit: {exit_code}")
    except Exception:
        log.warning("Timeout waiting for shutdown; forcing")
        container.kill()  # SIGKILL
```

For tunnel deletion, also wait for CF to show 0 connections before calling DELETE on the CF API.

## Orphan Detection

Containers that claim our labels but have no DB entry:

```python
async def find_orphan_sidecars() -> list[Container]:
    all_managed = docker.containers.list(
        all=True,
        filters={"label": "tunnel-manager.managed=true"}
    )
    orphans = []
    for c in all_managed:
        tunnel_id = c.labels.get("tunnel-manager.tunnel.id")
        if not tunnel_id or not db.get_tunnel(int(tunnel_id)):
            orphans.append(c)
    return orphans
```

Surface in GUI with cleanup action.

## Resource Limits

Per sidecar defaults:
- Memory: 128 MB (cloudflared typically uses 25-50 MB)
- CPU: no hard limit (cloudflared is I/O bound)
- Logs: json-file driver, 10 MB rotation, 3 files kept

These are configurable in `app_settings`.

## Security Hardening on Spawned Containers

Every sidecar spawned with:

```python
# docker-py arguments
read_only=True,              # root filesystem read-only
cap_drop=["ALL"],            # drop all capabilities
security_opt=["no-new-privileges:true"],
# No volumes mounted
# No ports published
# User: runs as cloudflared user by default in the image
```

If a sidecar needs to write anything (rare), add specific tmpfs mounts for just those paths.
