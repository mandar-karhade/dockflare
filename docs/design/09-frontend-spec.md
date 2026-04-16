# 09 — Frontend Specification

## Stack

- **Framework:** React 18+ with TypeScript (strict mode)
- **Build:** Vite
- **Routing:** React Router v6
- **Data fetching:** TanStack Query v5
- **Forms:** React Hook Form + Zod schema validation
- **UI components:** shadcn/ui (Radix UI + Tailwind)
- **Styling:** Tailwind CSS
- **Icons:** lucide-react
- **Realtime:** native WebSocket with reconnection wrapper
- **State:** minimal local state; TanStack Query as source of truth for server state

## Design System

### Theme
- Default: light, with dark mode toggle
- Colors follow shadcn/ui defaults
- System font stack; no custom webfonts (smaller bundle, faster load)

### Layout Primitives
- AppShell: header + sidebar + main content area
- Cards for grouped content
- Tables for lists of resources
- Modals for create/edit forms
- Toasts for notifications

### Status Indicators (consistent across views)

| State | Color | Icon |
|-------|-------|------|
| `active` / healthy | green | CheckCircle |
| `provisioning` / pending | blue | Loader (spinning) |
| `warning` / degraded | yellow | AlertTriangle |
| `error` / failed | red | AlertCircle |
| `disabled` | gray | CircleDashed |
| `orphaned` | orange | Unlink |

## Information Architecture

```
/                                Dashboard (overview)
/tunnels                         Tunnel list
/tunnels/:id                     Tunnel detail
/tunnels/:id/routes              Routes for tunnel
/routes                          All routes (flat list)
/routes/:id                      Route detail
/containers                      Container list
/containers/:id                  Container detail
/credentials                     CF API credentials
/zones                           Zone list
/drift                           Drift findings
/orphans                         Orphan resources
/audit                           Audit log
/settings                        Settings
/bootstrap                       First-run flow (conditional redirect)
/login                           Login page
```

## Views

### Bootstrap (first-run)

Multi-step wizard shown when `bootstrap.completed` is false.

**Step 1: Welcome**
- Explain what the manager does
- Link to docs
- Button: Get Started

**Step 2: CF API Token**
- Text input (masked, show/hide toggle)
- Inline help with link to CF token creation page
- Scope requirements checklist (populated after verification)
- Button: Verify & Continue

**Step 3: Account Selection**
- List of accounts accessible to the token
- Radio selector
- Preview: "X zones available, Y tunnels detected"

**Step 4: Import or Fresh Start**
- Two cards:
  - "Import existing tunnels" (if any detected)
  - "Start fresh"
- If import selected, show detected tunnels with checkboxes
- Per-tunnel option: "Take over existing cloudflared sidecar" (if detected)

**Step 5: Review & Complete**
- Summary of what will be imported
- Policy defaults (with ability to change)
- Button: Complete Setup

### Dashboard

Top-level overview. Layout:

```
┌─────────────────────────────────────────────────────────┐
│  HEADER: tunnel-manager    [🔔3] [user] [theme]          │
├─────────┬───────────────────────────────────────────────┤
│         │                                                │
│ SIDEBAR │  Dashboard                                     │
│         │                                                │
│ - Home  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐│
│ Tunnels │  │Tunnels  │ │Routes   │ │Drift    │ │Rotate││
│ Routes  │  │12       │ │34       │ │2        │ │3 due ││
│ Cont.   │  │11 ok    │ │32 ok    │ │1 err    │ │     ││
│ Zones   │  └─────────┘ └─────────┘ └─────────┘ └──────┘│
│ Drift   │                                                │
│ Audit   │  Recent Activity                              │
│ Settings│  [activity feed of last 10 events]            │
│         │                                                │
│         │  Tunnels by Project                           │
│         │  [grouped tunnel cards]                       │
└─────────┴───────────────────────────────────────────────┘
```

### Tunnels List

Grouped by `primary_compose_project`. Each group shows:
- Project name header with expand/collapse
- Tunnel cards with:
  - Name, CF tunnel ID (truncated)
  - Status badge
  - Route count
  - CF edge connections
  - Next rotation due
  - Quick actions: restart, rotate, delete

### Tunnel Detail

Tabs:

1. **Overview**
   - Key info (name, CF ID, account, created date)
   - Token info (last 4, fetched at, deployed at, policy)
   - Sidecar info (container ID, status, image)
   - Health panel (edge connections live)
   - Actions: Rotate, Restart Sidecar, Force Recreate, Delete

2. **Routes**
   - List of routes with drag-reorder for priority
   - "Test Match" widget: enter hostname + path, see which rule fires
   - Add Route button

3. **Logs**
   - Tail of cloudflared container logs
   - Live tail toggle (follows new lines)

4. **Rotation History**
   - Timeline of rotation events
   - Success/failed/noop with details
   - Downtime metric per rotation

### Routes (All)

Flat table with filtering:
- Filter by project, tunnel, status, zone
- Columns: hostname, path, tunnel, target, status, last healthy

### Route Detail

- Hostname and CF public URL (with copy button)
- Target container (link to detail)
- Priority with drag or manual edit
- Origin request options (editable)
- DNS record info (with "View in CF" link)
- Status history
- Actions: Enable, Disable, Edit, Delete

### Add/Edit Route Modal

Multi-step form:

```
Step 1: Destination
  - Container picker (grouped by project)
    - Shows available containers with exposed ports
  - Target port (dropdown of exposed ports, or manual entry)
  - Target scheme (http/https/tcp/ssh/rdp/unix)
  - Path prefix (optional)

Step 2: Public URL
  - Hostname text input with zone auto-detection
  - Path regex (optional, advanced toggle)
  - Priority (auto or manual)

Step 3: Advanced (collapsed by default)
  - No TLS verify
  - HTTP host header override
  - Origin server name (SNI)
  - Connect timeout
  - HTTP/2 origin

Step 4: Review
  - Summary of all fields
  - Conflict check preview (calls /check-conflict endpoint)
  - If conflict: resolution picker
  - Create button
```

### Containers List

- Grouped by compose project
- Filter: running/all, managed/unmanaged
- Columns: name, image, ports, networks, tunnel-managed indicator
- Row action: "Add Route for this Container" (opens add-route modal pre-filled)

### Credentials

- List of CF credentials (usually 1 active, maybe 1 archived post-rotation)
- Active indicator
- Actions: Verify, Rotate, Delete (non-active only)

### Zones

Read-only list of zones from cache:
- Name, status, plan, refreshed_at
- Refresh button (force CF API refresh)

### Drift Findings

- Filter by severity, type, scan ID
- List of unresolved findings with resolution actions
- Per-finding expandable row showing expected vs actual diff
- Bulk actions: "Reconcile all to DB", "Accept all external"

### Orphans

Two sub-tabs:
1. Orphan DNS records
2. Orphan CF tunnels
3. Orphan sidecar containers

For each: list with checkboxes, bulk actions (adopt/delete).

### Audit Log

- Searchable, filterable log
- Filters: actor, action, entity, date range
- Each row expandable to show before/after JSON
- Export to CSV/JSON

### Settings

Sections:

1. **General**
   - Theme
   - Language (future)

2. **Policies**
   - DNS conflict default
   - Orphan DNS default
   - Drift resolution default
   - Require confirmation for destructive

3. **Rotation**
   - Global scheduler enabled
   - Stagger jitter
   - Default rotation policy for new tunnels

4. **Notifications**
   - Webhook URL
   - Events to notify on (checkboxes)

5. **Backup (Litestream)**
   - Enabled status
   - R2 bucket
   - Last backup timestamp

6. **Access Control**
   - User management (future v2)
   - API tokens for programmatic access

## Component Architecture

```
src/
├── api/
│   ├── client.ts            # fetch wrapper with auth
│   ├── tunnels.ts           # typed endpoints
│   ├── routes.ts
│   ├── containers.ts
│   ├── ...
│   └── websocket.ts         # WS client with reconnect
│
├── hooks/
│   ├── useTunnels.ts        # TanStack Query hooks per resource
│   ├── useRoutes.ts
│   ├── useWebSocket.ts
│   └── useAuth.ts
│
├── components/
│   ├── ui/                  # shadcn/ui components
│   ├── common/
│   │   ├── StatusBadge.tsx
│   │   ├── DateDisplay.tsx
│   │   ├── TokenDisplay.tsx # shows ••••1234
│   │   ├── ConflictDialog.tsx
│   │   └── ConfirmDialog.tsx
│   ├── tunnels/
│   │   ├── TunnelList.tsx
│   │   ├── TunnelCard.tsx
│   │   ├── TunnelDetail.tsx
│   │   ├── TunnelHealthPanel.tsx
│   │   └── CreateTunnelModal.tsx
│   ├── routes/
│   │   ├── RouteList.tsx
│   │   ├── RouteRow.tsx
│   │   ├── AddRouteModal.tsx
│   │   ├── RouteReorderList.tsx
│   │   └── TestMatchWidget.tsx
│   ├── containers/
│   ├── drift/
│   └── layout/
│       ├── AppShell.tsx
│       ├── Sidebar.tsx
│       └── Header.tsx
│
├── pages/
│   ├── Dashboard.tsx
│   ├── TunnelsPage.tsx
│   ├── TunnelDetailPage.tsx
│   ├── RoutesPage.tsx
│   ├── ...
│   └── BootstrapPage.tsx
│
├── lib/
│   ├── zod-schemas.ts       # shared validation
│   ├── format.ts            # date/duration formatters
│   └── utils.ts
│
├── types/
│   └── api.ts               # generated from OpenAPI or hand-written
│
└── main.tsx
```

## Real-Time Updates

WebSocket connection managed by a single `useWebSocket` hook that wires events into TanStack Query cache invalidation:

```typescript
useEffect(() => {
  const ws = new WebSocket("/api/v1/ws/events");
  
  ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    
    switch (event.type) {
      case "tunnel.changed":
        queryClient.invalidateQueries({ queryKey: ["tunnels"] });
        queryClient.invalidateQueries({ queryKey: ["tunnel", event.data.id] });
        break;
      case "route.changed":
        queryClient.invalidateQueries({ queryKey: ["routes"] });
        break;
      case "tunnel.health":
        queryClient.setQueryData(
          ["tunnel-health", event.data.tunnel_id],
          event.data
        );
        break;
      case "drift.detected":
        queryClient.invalidateQueries({ queryKey: ["drift"] });
        toast.warning("New drift findings detected");
        break;
      case "notification":
        toast[event.data.level](event.data.message);
        break;
    }
  };
  
  // Reconnection logic with exponential backoff
  ws.onclose = () => scheduleReconnect();
  
  return () => ws.close();
}, []);
```

## Form Validation

Shared Zod schemas in `lib/zod-schemas.ts`, used both client-side (React Hook Form) and matched by backend Pydantic:

```typescript
export const hostnameSchema = z.string()
  .min(1)
  .max(253)
  .regex(/^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$/, "Invalid hostname");

export const createRouteSchema = z.object({
  tunnel_id: z.number().int().positive(),
  hostname: hostnameSchema,
  path_regex: z.string().max(500).nullable().optional(),
  target_compose_project: z.string().optional(),
  target_compose_service: z.string().optional(),
  target_container_name: z.string().optional(),
  target_scheme: z.enum(["http", "https", "tcp", "ssh", "rdp", "unix"]),
  target_port: z.number().int().min(1).max(65535).optional(),
  // ...
});

export type CreateRouteInput = z.infer<typeof createRouteSchema>;
```

## Data Fetching Patterns

TanStack Query with typed hooks:

```typescript
export function useTunnels(filters?: TunnelFilters) {
  return useQuery({
    queryKey: ["tunnels", filters],
    queryFn: () => api.tunnels.list(filters),
    staleTime: 30_000,
  });
}

export function useTunnel(id: number) {
  return useQuery({
    queryKey: ["tunnel", id],
    queryFn: () => api.tunnels.get(id),
    staleTime: 30_000,
  });
}

export function useTunnelHealth(id: number) {
  return useQuery({
    queryKey: ["tunnel-health", id],
    queryFn: () => api.tunnels.health(id),
    refetchInterval: 30_000,  // fallback if WS is down
  });
}

export function useCreateRoute() {
  return useMutation({
    mutationFn: (input: CreateRouteInput) => api.routes.create(input),
    onSuccess: (_, input) => {
      queryClient.invalidateQueries({ queryKey: ["tunnel", input.tunnel_id] });
      queryClient.invalidateQueries({ queryKey: ["routes"] });
      toast.success("Route created");
    },
    onError: handleApiError,
  });
}
```

## Error Handling

Centralized error handler:

```typescript
export function handleApiError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.body?.code?.startsWith("conflict_")) {
      // Show conflict resolution dialog
      openConflictDialog(error.body);
      return;
    }
    if (error.status === 401) {
      router.navigate("/login");
      return;
    }
    toast.error(error.body?.title || "Request failed");
    return;
  }
  toast.error("Unexpected error occurred");
  console.error(error);
}
```

## Routing Guards

```typescript
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/bootstrap" element={<BootstrapPage />} />
  <Route element={<RequireAuth />}>
    <Route element={<RequireBootstrap />}>
      <Route element={<AppShell />}>
        <Route path="/" element={<Dashboard />} />
        {/* ... */}
      </Route>
    </Route>
  </Route>
</Routes>
```

`RequireBootstrap` checks `GET /api/v1/info` and redirects to `/bootstrap` if not completed.

## Accessibility

- All interactive elements keyboard reachable
- ARIA labels on icon-only buttons
- Focus visible in all states (Tailwind default ring)
- Color-blind safe: always pair color with icon/text for status
- `prefers-reduced-motion` respected for animations
- Modal focus trapping via Radix primitives

## Testing

- Component tests with Vitest + React Testing Library
- E2E tests with Playwright, covering bootstrap flow and CRUD for each resource
- Mock Service Worker for test-time API mocking

## Build Output

```
dist/
├── index.html
├── assets/
│   ├── index-[hash].js
│   ├── index-[hash].css
│   └── ...
```

Served by the FastAPI app as static files under `/`. API endpoints under `/api/v1/*`.
