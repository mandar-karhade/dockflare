import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Moon, Settings2, Sun } from "lucide-react";
import { apiFetch } from "./api/client";
import { useEffect, useState } from "react";

// ---- Types ----

interface ContainerEntry {
  container_id: string;
  name: string;
  service: string | null;
  image: string;
  status: string;
  ports: number[];
  networks: string[];
  is_cloudflared: boolean;
  hostname: string | null;
  target_service_url: string | null;
  tunnel_name: string | null;
  tunnel_id: string | null;
}

interface ProjectTunnel {
  tunnel_id: string;
  name: string;
  status: string;
  connections: number;
  machine: string;
  route_count: number;
}

interface Project {
  project: string;
  networks: string[];
  containers: ContainerEntry[];
  tunnel: ProjectTunnel | null;
}

interface TunnelRoute {
  hostname: string;
  service: string;
  path: string | null;
}

interface TunnelInfo {
  tunnel_id: string;
  name: string;
  status: string;
  connections: number;
  is_local: boolean;
  machine: string;
  origin_ip: string | null;
  routes: TunnelRoute[];
  sidecar: { name: string; project: string | null; networks: string[]; status: string } | null;
}

interface DashboardResponse {
  projects: Project[];
  standalone: ContainerEntry[];
  tunnels: TunnelInfo[];
  machines: Record<string, number>;
  local_ip: string | null;
  total_tunnels: number;
  total_projects: number;
}

interface ZonesResponse {
  zones: { zone_id: string; zone_name: string; status: string; plan: string }[];
  total: number;
}

interface ExportedConfig {
  version: number;
  tunnel_name: string;
  tunnel_id: string;
  exported_at: string;
  ingress: Record<string, unknown>[];
}

interface CreatedTunnel {
  tunnel_id: string;
  name: string;
}

interface DraftTunnel {
  tunnel_id: string;
  name: string;
  project: string;
  status: string;
  connections: number;
  machine: string;
  routes: TunnelRoute[];
}

// Container types for service picker
interface ContainersResponse {
  projects: Record<string, { compose_service: string | null; name: string; exposed_ports: number[]; status: string; is_cloudflared: boolean }[]>;
}

type Tab = "dashboard" | "tunnels" | "zones";
type Theme = "light" | "dark";
type DashboardColumnId = (typeof DASHBOARD_COLUMNS)[number]["id"];
type DashboardColumnVisibility = Record<DashboardColumnId, boolean>;

// ---- Components ----

const THEME_STORAGE_KEY = "dockflare-theme";
const DASHBOARD_COLUMN_STORAGE_KEY = "dockflare-dashboard-columns";

const DASHBOARD_COLUMNS = [
  { id: "tunnel", label: "Tunnel", canHide: true },
  { id: "status", label: "Status", canHide: true },
  { id: "project", label: "Project", canHide: true },
  { id: "network", label: "Network", canHide: true },
  { id: "container", label: "Container", canHide: true },
  { id: "service", label: "Service", canHide: true },
  { id: "ports", label: "Ports", canHide: true },
  { id: "hostname", label: "Hostname", canHide: true },
  { id: "target", label: "Target", canHide: true },
  { id: "path", label: "Path", canHide: true },
  { id: "actions", label: "Actions", canHide: false },
] as const;

const getInitialTheme = (): Theme => {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "dark";
};

const applyTheme = (theme: Theme) => {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
};

const defaultDashboardColumns = (): DashboardColumnVisibility =>
  Object.fromEntries(DASHBOARD_COLUMNS.map((column) => [column.id, true])) as DashboardColumnVisibility;

const getInitialDashboardColumns = (): DashboardColumnVisibility => {
  const defaults = defaultDashboardColumns();
  if (typeof window === "undefined") return defaults;
  try {
    const stored = window.sessionStorage.getItem(DASHBOARD_COLUMN_STORAGE_KEY);
    if (!stored) return defaults;
    const parsed = JSON.parse(stored) as Partial<Record<DashboardColumnId, unknown>>;
    return DASHBOARD_COLUMNS.reduce<DashboardColumnVisibility>((columns, column) => {
      const storedValue = parsed[column.id];
      columns[column.id] = column.canHide && typeof storedValue === "boolean" ? storedValue : true;
      return columns;
    }, defaults);
  } catch {
    return defaults;
  }
};

const ThemeToggle = ({ theme, onToggle }: { theme: Theme; onToggle: () => void }) => {
  const isDark = theme === "dark";
  const Icon = isDark ? Moon : Sun;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={`Switch to ${isDark ? "light" : "dark"} theme`}
      title={`Switch to ${isDark ? "light" : "dark"} theme`}
      className="ml-auto inline-flex h-8 items-center gap-2 rounded border bg-background px-2.5 text-xs font-medium text-foreground shadow-sm transition-colors hover:bg-muted"
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {isDark ? "Dark" : "Light"}
    </button>
  );
};

const ColumnVisibilityMenu = ({
  columns,
  onToggle,
  onReset,
}: {
  columns: DashboardColumnVisibility;
  onToggle: (column: DashboardColumnId) => void;
  onReset: () => void;
}) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="inline-flex h-7 items-center gap-1.5 rounded border bg-background px-2.5 text-xs font-medium transition-colors hover:bg-muted"
      >
        <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
        Columns
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-44 rounded border bg-popover p-2 text-popover-foreground shadow-lg">
          <div className="space-y-1">
            {DASHBOARD_COLUMNS.map((column) => (
              <label
                key={column.id}
                className={`flex items-center gap-2 rounded px-1.5 py-1 text-xs ${column.canHide ? "hover:bg-muted" : "text-muted-foreground"}`}
              >
                <input
                  type="checkbox"
                  checked={columns[column.id]}
                  disabled={!column.canHide}
                  onChange={() => onToggle(column.id)}
                />
                <span>{column.label}</span>
              </label>
            ))}
          </div>
          <button
            type="button"
            onClick={onReset}
            className="mt-2 w-full rounded border px-2 py-1 text-xs font-medium hover:bg-muted"
          >
            Reset View
          </button>
        </div>
      )}
    </div>
  );
};

const Dot = ({ color }: { color: string }) => <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${color}`} />;
const StatusDot = ({ status }: { status: string }) => {
  const c = status === "running" || status === "connected" || status === "active" ? "bg-green-500" : status === "exited" || status === "disconnected" ? "bg-red-400" : "bg-yellow-400";
  return <Dot color={c} />;
};

const Modal = ({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-lg rounded-lg border bg-background p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">X</button>
        </div>
        {children}
      </div>
    </div>
  );
};

const Btn = ({ children, onClick, variant = "default", disabled }: {
  children: React.ReactNode; onClick?: () => void; variant?: "default" | "primary" | "danger" | "ghost"; disabled?: boolean;
}) => {
  const v = { default: "border hover:bg-muted", primary: "bg-primary text-primary-foreground hover:bg-primary/90", danger: "text-red-500 hover:bg-red-50 dark:hover:bg-red-950 border-transparent", ghost: "text-primary hover:bg-primary/10 border-transparent" }[variant];
  return <button onClick={onClick} disabled={disabled} className={`rounded border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${v}`}>{children}</button>;
};

// ---- Service Picker ----

const useServiceOptions = () => {
  const { data } = useQuery({ queryKey: ["containers"], queryFn: () => apiFetch<ContainersResponse>("/containers"), staleTime: 60_000 });
  if (!data) return [];
  const opts: { label: string; value: string; project: string; service: string }[] = [];
  for (const [project, containers] of Object.entries(data.projects)) {
    for (const c of containers) {
      if (c.is_cloudflared || c.status !== "running") continue;
      const svc = c.compose_service ?? c.name;
      for (const port of c.exposed_ports.length > 0 ? c.exposed_ports : [0]) {
        opts.push({ label: port ? `${project}/${svc}:${String(port)}` : `${project}/${svc}`, value: port ? `http://${svc}:${String(port)}` : `http://${svc}`, project, service: svc });
      }
    }
  }
  return opts;
};

const ServicePicker = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => {
  const options = useServiceOptions();
  const [open, setOpen] = useState(false);
  const grouped: Record<string, typeof options> = {};
  for (const o of options) (grouped[o.project] ??= []).push(o);
  return (
    <div className="relative">
      <input value={value} onChange={(e) => onChange(e.target.value)} onFocus={() => setOpen(true)} placeholder="http://service:port" className="w-full rounded border bg-background px-2 py-1 font-mono text-xs" />
      {open && options.length > 0 && (
        <div className="absolute left-0 top-full z-10 mt-1 max-h-48 w-72 overflow-y-auto rounded border bg-background shadow-lg">
          {Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([proj, os]) => (
            <div key={proj}>
              <div className="sticky top-0 bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">{proj}</div>
              {os.map((o, i) => (
                <button key={`${o.value}-${String(i)}`} onClick={() => { onChange(o.value); setOpen(false); }} className="flex w-full gap-2 px-2 py-1 text-left text-xs hover:bg-muted/50">
                  <span className="font-medium">{o.service}</span>
                  <span className="ml-auto font-mono text-muted-foreground/60">{o.value}</span>
                </button>
              ))}
            </div>
          ))}
          <button onClick={() => setOpen(false)} className="w-full border-t px-2 py-1 text-[10px] text-muted-foreground">Close</button>
        </div>
      )}
    </div>
  );
};

type EditableRoute = { hostname: string; service: string; path: string };

const routeInputClass = "w-full min-w-44 rounded border bg-background px-2 py-1 font-mono text-xs";
const pathInputClass = "w-24 rounded border bg-background px-2 py-1 font-mono text-xs";

const sanitizeHostname = (value: string) => {
  const raw = value.trim();
  if (!raw) return "";
  try {
    return new URL(raw.includes("://") ? raw : `http://${raw}`).hostname.replace(/\.$/, "");
  } catch {
    return ((raw.replace(/^https?:\/\//, "").split("/")[0] ?? "").split(":")[0] ?? "").replace(/\.$/, "");
  }
};

const toApiRoute = (route: EditableRoute) => ({
  hostname: sanitizeHostname(route.hostname),
  service: route.service.trim(),
  path: route.path.trim() || null,
});

// ---- Modals ----

const DeleteTunnelModal = ({ tunnelId, tunnelName, onClose, onConfirm, isPending }: { tunnelId: string; tunnelName: string; onClose: () => void; onConfirm: (id: string) => void; isPending: boolean }) => {
  const [text, setText] = useState("");
  return (
    <Modal open={true} onClose={onClose} title="Delete Tunnel">
      <p className="mb-2 text-sm text-muted-foreground">This will permanently delete the tunnel, DNS records, and sidecar.</p>
      <p className="mb-3 text-sm">Type <span className="rounded bg-muted px-1.5 py-0.5 font-mono font-semibold">{tunnelName}</span> to confirm.</p>
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder={tunnelName} className="mb-4 w-full rounded border bg-background px-3 py-2 font-mono text-sm" autoFocus />
      <div className="flex justify-end gap-2"><Btn onClick={onClose}>Cancel</Btn><Btn onClick={() => onConfirm(tunnelId)} disabled={text !== tunnelName || isPending} variant="primary">{isPending ? "Deleting..." : "Delete"}</Btn></div>
    </Modal>
  );
};

const ImportTunnelModal = ({ open, onClose }: { open: boolean; onClose: () => void }) => {
  const qc = useQueryClient();
  const [json, setJson] = useState("");
  const [sidecar, setSidecar] = useState(false);
  const [project, setProject] = useState("");
  const [service, setService] = useState("");
  const [err, setErr] = useState("");
  const mut = useMutation({ mutationFn: (body: unknown) => apiFetch("/tunnels/import", { method: "POST", body: JSON.stringify(body) }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); setJson(""); onClose(); } });
  const go = () => { try { const p = JSON.parse(json) as ExportedConfig; if (!p.tunnel_name || !p.ingress) { setErr("Missing tunnel_name or ingress"); return; } setErr(""); mut.mutate({ tunnel_name: p.tunnel_name, ingress: p.ingress, spawn_sidecar: sidecar, ...(project ? { target_compose_project: project } : {}), ...(service ? { target_compose_service: service } : {}) }); } catch { setErr("Invalid JSON"); } };
  return (
    <Modal open={open} onClose={onClose} title="Import Tunnel Config">
      <p className="mb-2 text-sm text-muted-foreground">Paste exported config to create a new tunnel with the same routes and a fresh token.</p>
      <textarea value={json} onChange={(e) => setJson(e.target.value)} rows={6} placeholder="Paste JSON..." className="mb-3 w-full rounded border bg-background px-3 py-2 font-mono text-xs" />
      <div className="mb-3 flex items-center gap-2"><input type="checkbox" id="imp-sc" checked={sidecar} onChange={(e) => setSidecar(e.target.checked)} /><label htmlFor="imp-sc" className="text-sm">Spawn sidecar</label></div>
      {sidecar && <div className="mb-3 grid grid-cols-2 gap-2"><div><label className="text-xs">Project</label><input value={project} onChange={(e) => setProject(e.target.value)} className="w-full rounded border bg-background px-2 py-1 text-sm" /></div><div><label className="text-xs">Service</label><input value={service} onChange={(e) => setService(e.target.value)} className="w-full rounded border bg-background px-2 py-1 text-sm" /></div></div>}
      {err && <p className="mb-2 text-xs text-destructive">{err}</p>}
      <div className="flex justify-end gap-2"><Btn onClick={onClose}>Cancel</Btn><Btn onClick={go} disabled={!json || mut.isPending} variant="primary">{mut.isPending ? "Importing..." : "Import & Create"}</Btn></div>
    </Modal>
  );
};

const CreateTunnelModal = ({
  open,
  onClose,
  project,
  service,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  project?: string;
  service?: string;
  onCreated?: (tunnel: CreatedTunnel) => void;
}) => {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [manualProject, setManualProject] = useState("");
  const [manualService, setManualService] = useState("");
  const isProjectScoped = Boolean(project);
  const mut = useMutation({
    mutationFn: (b: { name: string; primary_compose_project?: string; primary_compose_service?: string }) => apiFetch<CreatedTunnel>("/tunnels", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      setName("");
      onCreated?.(created);
      onClose();
    },
  });
  const create = () => {
    mut.mutate({
      name,
      ...(project ? { primary_compose_project: project } : manualProject ? { primary_compose_project: manualProject } : {}),
      ...(service ? { primary_compose_service: service } : manualService ? { primary_compose_service: manualService } : {}),
    });
  };
  return (
    <Modal open={open} onClose={onClose} title="Create New Tunnel">
      <div className="space-y-3">
        <div><label htmlFor="tunnel-name" className="mb-1 block text-sm font-medium">Name</label><input id="tunnel-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. my-project" className="w-full rounded border bg-background px-3 py-2 text-sm" /></div>
        {!isProjectScoped && (
          <>
            <div><label htmlFor="tunnel-project" className="mb-1 block text-sm font-medium">Compose Project</label><input id="tunnel-project" value={manualProject} onChange={(e) => setManualProject(e.target.value)} className="w-full rounded border bg-background px-3 py-2 text-sm" /></div>
            <div><label htmlFor="tunnel-service" className="mb-1 block text-sm font-medium">Service</label><input id="tunnel-service" value={manualService} onChange={(e) => setManualService(e.target.value)} className="w-full rounded border bg-background px-3 py-2 text-sm" /></div>
          </>
        )}
        <div className="flex justify-end gap-2"><Btn onClick={onClose}>Cancel</Btn><Btn onClick={create} disabled={!name || mut.isPending} variant="primary">{mut.isPending ? "Creating..." : "Create"}</Btn></div>
      </div>
    </Modal>
  );
};

// ---- Dashboard View ----

const DashboardView = () => {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["dashboard"], queryFn: () => apiFetch<DashboardResponse>("/dashboard") });
  const [filterMachine, setFilterMachine] = useState("all");
  const [showCreate, setShowCreate] = useState(false);
  const [projectCreate, setProjectCreate] = useState<{ project: Project; routes: TunnelRoute[] } | null>(null);
  const [draftTunnel, setDraftTunnel] = useState<DraftTunnel | null>(null);
  const [routeDrafts, setRouteDrafts] = useState<Record<string, EditableRoute>>({});
  const [editingRouteKey, setEditingRouteKey] = useState<string | null>(null);
  const [savingRouteKey, setSavingRouteKey] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [visibleColumns, setVisibleColumns] = useState<DashboardColumnVisibility>(getInitialDashboardColumns);

  const [confirmRecreate, setConfirmRecreate] = useState<string | null>(null);

  useEffect(() => {
    window.sessionStorage.setItem(DASHBOARD_COLUMN_STORAGE_KEY, JSON.stringify(visibleColumns));
  }, [visibleColumns]);

  const deleteMut = useMutation({
    mutationFn: (id: string) => apiFetch(`/tunnels/${id}`, { method: "DELETE" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); setConfirmDelete(null); },
  });

  const recreateMut = useMutation({
    mutationFn: (id: string) => apiFetch(`/tunnels/${id}/recreate`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); setConfirmRecreate(null); },
  });

  const refreshAllMut = useMutation({
    mutationFn: () => apiFetch("/dashboard/refresh", { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); },
  });

  const refreshTunnelMut = useMutation({
    mutationFn: (id: string) => apiFetch(`/tunnels/${id}/refresh`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); },
  });

  const saveIngressMut = useMutation({
    mutationFn: ({ tunnelId, rules }: { tunnelId: string; rules: ReturnType<typeof toApiRoute>[] }) =>
      apiFetch(`/tunnels/${tunnelId}/ingress`, { method: "PUT", body: JSON.stringify(rules) }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dashboard"] }); },
  });

  const handleExport = async (tunnelId: string) => {
    const config = await apiFetch<ExportedConfig>(`/tunnels/${tunnelId}/export`);
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `tunnel-${config.tunnel_name}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const buildDraftRoutes = (project: Project) => project.containers
    .filter((c) => !c.is_cloudflared && c.status === "running")
    .flatMap((c) => {
      const target = c.service ?? c.name;
      const ports = c.ports.length > 0 ? c.ports : [0];
      return ports.map((port) => ({
        hostname: "",
        service: port ? `http://${target}:${String(port)}` : `http://${target}`,
        path: null,
      }));
    });

  const openProjectCreate = (project: Project) => {
    setProjectCreate({ project, routes: buildDraftRoutes(project) });
  };
  const defaultTarget = (container: ContainerEntry) => {
    const target = container.service ?? container.name;
    const port = container.ports[0];
    return port ? `http://${target}:${String(port)}` : `http://${target}`;
  };
  const getTunnel = (tunnelId: string) => data?.tunnels.find((t) => t.tunnel_id === tunnelId) ?? (draftTunnel?.tunnel_id === tunnelId ? draftTunnel : null);
  const getTunnelRoutes = (tunnelId: string) => getTunnel(tunnelId)?.routes ?? [];
  const getDraft = (key: string, initial: EditableRoute) => routeDrafts[key] ?? initial;
  const updateDraft = (key: string, initial: EditableRoute, patch: Partial<EditableRoute>) => {
    setRouteDrafts((current) => ({ ...current, [key]: { ...(current[key] ?? initial), ...patch } }));
  };
  const saveRoute = (routeKey: string, tunnelId: string, originalService: string, route: EditableRoute) => {
    const next = getTunnelRoutes(tunnelId)
      .filter((item) => item.service !== originalService && item.service !== route.service)
      .map((item) => toApiRoute({ hostname: item.hostname, service: item.service, path: item.path ?? "" }));
    if (route.hostname.trim() && route.service.trim()) next.push(toApiRoute(route));
    setSavingRouteKey(routeKey);
    saveIngressMut.mutate(
      { tunnelId, rules: next },
      {
        onSuccess: () => setEditingRouteKey((current) => (current === routeKey ? null : current)),
        onSettled: () => setSavingRouteKey((current) => (current === routeKey ? null : current)),
      },
    );
  };
  const deleteRoute = (routeKey: string, tunnelId: string, originalService: string) => {
    setSavingRouteKey(routeKey);
    saveIngressMut.mutate(
      {
        tunnelId,
        rules: getTunnelRoutes(tunnelId)
          .filter((item) => item.service !== originalService)
          .map((item) => toApiRoute({ hostname: item.hostname, service: item.service, path: item.path ?? "" })),
      },
      { onSettled: () => setSavingRouteKey((current) => (current === routeKey ? null : current)) },
    );
  };
  const startEditing = (routeKey: string) => {
    setEditingRouteKey(routeKey);
  };
  const isColumnVisible = (column: DashboardColumnId) => visibleColumns[column];
  const toggleColumn = (column: DashboardColumnId) => {
    const columnConfig = DASHBOARD_COLUMNS.find((item) => item.id === column);
    if (!columnConfig?.canHide) return;
    setVisibleColumns((current) => ({ ...current, [column]: !current[column] }));
  };

  if (isLoading) return <p className="text-muted-foreground">Loading...</p>;
  if (error) return <p className="text-destructive">Failed to load dashboard</p>;
  if (!data) return null;

  // Build flat rows: each container is a row, grouped by project
  // Filter tunnels by machine, but show all projects (containers are always local)
  const tunnelsByMachine = filterMachine === "all" ? data.tunnels : data.tunnels.filter((t) => t.machine === filterMachine);
  return (
    <div>
      {/* Toolbar */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Btn onClick={() => setShowCreate(true)} variant="primary">New Tunnel</Btn>
        <Btn onClick={() => setShowImport(true)}>Import</Btn>
        <Btn onClick={() => refreshAllMut.mutate()} disabled={refreshAllMut.isPending}>{refreshAllMut.isPending ? "Refreshing..." : "Refresh"}</Btn>
        <ColumnVisibilityMenu columns={visibleColumns} onToggle={toggleColumn} onReset={() => setVisibleColumns(defaultDashboardColumns())} />
        <div className="ml-auto flex gap-0.5 rounded-lg bg-muted p-0.5">
          <button onClick={() => setFilterMachine("all")} className={`rounded px-2 py-1 text-xs font-medium ${filterMachine === "all" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>All ({data.total_tunnels})</button>
          {Object.entries(data.machines).sort(([, a], [, b]) => b - a).map(([m, c]) => (
            <button key={m} onClick={() => setFilterMachine(m)} className={`rounded px-2 py-1 text-xs font-medium ${filterMachine === m ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>{m === "unknown" ? "offline" : m} ({c})</button>
          ))}
        </div>
      </div>

      {/* Main table */}
      <div className="overflow-auto rounded-lg border" style={{ maxHeight: "calc(100vh - 120px)" }}>
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="border-b bg-muted text-left text-xs font-medium text-muted-foreground">
              {isColumnVisible("tunnel") && <th className="px-3 py-2 whitespace-nowrap">Tunnel</th>}
              {isColumnVisible("status") && <th className="px-3 py-2 whitespace-nowrap">Status</th>}
              {isColumnVisible("project") && <th className="px-3 py-2 whitespace-nowrap">Project</th>}
              {isColumnVisible("network") && <th className="px-3 py-2 whitespace-nowrap">Network</th>}
              {isColumnVisible("container") && <th className="px-3 py-2 whitespace-nowrap">Container</th>}
              {isColumnVisible("service") && <th className="px-3 py-2 whitespace-nowrap">Service</th>}
              {isColumnVisible("ports") && <th className="px-3 py-2 whitespace-nowrap">Ports</th>}
              {isColumnVisible("hostname") && <th className="px-3 py-2 whitespace-nowrap">Hostname</th>}
              {isColumnVisible("target") && <th className="px-3 py-2 whitespace-nowrap">Target</th>}
              {isColumnVisible("path") && <th className="px-3 py-2 whitespace-nowrap">Path</th>}
              {isColumnVisible("actions") && <th className="px-3 py-2 whitespace-nowrap">Actions</th>}
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.projects.sort((a, b) => a.project.localeCompare(b.project)).map((p) => {
              const containers = p.containers.filter((c) => !c.is_cloudflared).sort((a, b) => (a.service ?? a.name).localeCompare(b.service ?? b.name));
              const tunnel = p.tunnel ?? (draftTunnel?.project === p.project ? draftTunnel : null);
              const rowCount = containers.length || 1;

              return containers.map((c, idx) => {
                const target = c.target_service_url ?? defaultTarget(c);
                const route = tunnel ? getTunnelRoutes(tunnel.tunnel_id).find((item) => item.service === target) : null;
                const initial: EditableRoute = { hostname: c.hostname ?? route?.hostname ?? "", service: route?.service ?? target, path: route?.path ?? "" };
                const draftKey = tunnel ? `${tunnel.tunnel_id}:${c.container_id}` : c.container_id;
                const draft = getDraft(draftKey, initial);
                const isEditing = editingRouteKey === draftKey;
                const isSaving = savingRouteKey === draftKey;
                return (
                <tr key={c.container_id} className={`hover:bg-muted/30 ${c.status !== "running" ? "opacity-50" : ""}`}>
                  {idx === 0 && (
                    <>
                      {/* Tunnel */}
                      {isColumnVisible("tunnel") && <td className="px-3 py-1.5 align-top whitespace-nowrap" rowSpan={rowCount}>
                        {tunnel ? (
                          <div>
                            <span className="text-xs font-medium">{tunnel.name}</span>
                            <div className="font-mono text-[10px] text-muted-foreground">{tunnel.machine !== "unknown" ? tunnel.machine : ""}</div>
                            <div className="mt-1">
                              <Btn onClick={() => setConfirmDelete(tunnel.tunnel_id)} variant="danger">Delete Tunnel</Btn>
                            </div>
                          </div>
                        ) : <Btn onClick={() => openProjectCreate(p)} variant="ghost">Create New</Btn>}
                      </td>}
                      {/* Status */}
                      {isColumnVisible("status") && <td className="px-3 py-1.5 align-top whitespace-nowrap" rowSpan={rowCount}>
                        {tunnel ? (
                          <div className="flex items-center gap-1.5">
                            <StatusDot status={tunnel.status} />
                            <span className="text-xs">{tunnel.status === "connected" ? `${String(tunnel.connections)} conn` : "offline"}</span>
                          </div>
                        ) : <span className="text-xs text-muted-foreground">-</span>}
                      </td>}
                      {/* Project */}
                      {isColumnVisible("project") && <td className="px-3 py-1.5 align-top font-medium whitespace-nowrap" rowSpan={rowCount}>
                        {p.project}
                      </td>}
                      {/* Network */}
                      {isColumnVisible("network") && <td className="px-3 py-1.5 align-top font-mono text-xs text-muted-foreground whitespace-nowrap" rowSpan={rowCount}>
                        {p.networks.join(", ") || "default"}
                      </td>}
                    </>
                  )}
                  {/* Container */}
                  {isColumnVisible("container") && <td className="px-3 py-1.5 whitespace-nowrap">
                    <div className="flex items-center gap-1.5">
                      <StatusDot status={c.status} />
                      <span className="font-mono text-xs">{c.name}</span>
                    </div>
                  </td>}
                  {/* Service */}
                  {isColumnVisible("service") && <td className="px-3 py-1.5 text-xs whitespace-nowrap">{c.service ?? "-"}</td>}
                  {/* Ports */}
                  {isColumnVisible("ports") && <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground whitespace-nowrap">{c.ports.length > 0 ? c.ports.join(", ") : "-"}</td>}
                  {/* Hostname */}
                  {isColumnVisible("hostname") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? (
                      <input value={draft.hostname} onChange={(e) => updateDraft(draftKey, initial, { hostname: e.target.value })} placeholder="app.example.com" className={routeInputClass} />
                    ) : draft.hostname ? (
                      <a href={`https://${sanitizeHostname(draft.hostname)}`} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline">{sanitizeHostname(draft.hostname)}</a>
                    ) : <span className="text-xs text-muted-foreground">-</span>}
                  </td>}
                  {/* Target */}
                  {isColumnVisible("target") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? <ServicePicker value={draft.service} onChange={(service) => updateDraft(draftKey, initial, { service })} /> : <span className="font-mono text-xs text-muted-foreground">{draft.service}</span>}
                  </td>}
                  {isColumnVisible("path") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? <input value={draft.path} onChange={(e) => updateDraft(draftKey, initial, { path: e.target.value })} placeholder="/api/*" className={pathInputClass} /> : <span className="font-mono text-xs text-muted-foreground">{draft.path || "-"}</span>}
                  </td>}
                  {/* Actions */}
                  {isColumnVisible("actions") && (
                    <td className="px-3 py-1.5 align-top whitespace-nowrap">
                        {tunnel ? (
                          <div className="flex gap-1">
                          {isEditing ? (
                            <Btn onClick={() => saveRoute(draftKey, tunnel.tunnel_id, initial.service, draft)} disabled={isSaving} variant="primary">{isSaving ? "Saving..." : "Save"}</Btn>
                          ) : (
                            <Btn onClick={() => startEditing(draftKey)} variant="ghost">Edit</Btn>
                          )}
                          <Btn onClick={() => deleteRoute(draftKey, tunnel.tunnel_id, initial.service)} disabled={isSaving} variant="danger">Delete Route</Btn>
                          <Btn onClick={() => void handleExport(tunnel.tunnel_id)} variant="ghost">Export</Btn>
                          <Btn onClick={() => refreshTunnelMut.mutate(tunnel.tunnel_id)} disabled={refreshTunnelMut.isPending} variant="ghost">{refreshTunnelMut.isPending ? "Refreshing..." : "Refresh"}</Btn>
                          <Btn onClick={() => setConfirmRecreate(tunnel.tunnel_id)} variant="ghost">Recreate</Btn>
                        </div>
                      ) : <span className="text-xs text-muted-foreground">-</span>}
                    </td>
                  )}
                </tr>
              );});
            })}

            {/* Unassociated tunnels (no local project) — column order: Tunnel, Status, Project, Network, Container, Service, Ports, Hostname, Target, Actions */}
            {tunnelsByMachine.filter((t) => !data.projects.some((p) => p.tunnel?.tunnel_id === t.tunnel_id)).map((t) => {
              const routes = t.routes;
              const rs = Math.max(routes.length, 1);
              if (routes.length === 0) {
                return (
                  <tr key={t.tunnel_id} className="hover:bg-muted/30 bg-muted/10">
                    {isColumnVisible("tunnel") && <td className="px-3 py-1.5 whitespace-nowrap"><span className="text-xs font-medium">{t.name}</span><div className="font-mono text-[10px] text-muted-foreground">{t.machine !== "unknown" ? t.machine : ""}</div></td>}
                    {isColumnVisible("status") && <td className="px-3 py-1.5"><div className="flex items-center gap-1.5"><StatusDot status={t.status} /><span className="text-xs">{t.status === "connected" ? `${String(t.connections)} conn` : "offline"}</span></div></td>}
                    {isColumnVisible("project") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("network") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("container") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("service") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("ports") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("hostname") && <td className="px-3 py-1.5 text-xs italic text-muted-foreground">no routes</td>}
                    {isColumnVisible("target") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("path") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                    {isColumnVisible("actions") && <td className="px-3 py-1.5 whitespace-nowrap">
                      <span className="text-xs text-muted-foreground">-</span>
                    </td>}
                  </tr>
                );
              }
              return routes.map((r, idx) => {
                const draftKey = `${t.tunnel_id}:orphan:${String(idx)}`;
                const initial: EditableRoute = { hostname: r.hostname, service: r.service, path: r.path ?? "" };
                const draft = getDraft(draftKey, initial);
                const isEditing = editingRouteKey === draftKey;
                const isSaving = savingRouteKey === draftKey;
                return (
                <tr key={`${t.tunnel_id}-${String(idx)}`} className="hover:bg-muted/30 bg-muted/10">
                  {idx === 0 && (
                    <>
                      {isColumnVisible("tunnel") && <td className="px-3 py-1.5 align-top whitespace-nowrap" rowSpan={rs}><span className="text-xs font-medium">{t.name}</span><div className="font-mono text-[10px] text-muted-foreground">{t.machine !== "unknown" ? t.machine : ""}</div></td>}
                      {isColumnVisible("status") && <td className="px-3 py-1.5 align-top whitespace-nowrap" rowSpan={rs}><div className="flex items-center gap-1.5"><StatusDot status={t.status} /><span className="text-xs">{t.status === "connected" ? `${String(t.connections)} conn` : "offline"}</span></div></td>}
                      {isColumnVisible("project") && <td className="px-3 py-1.5 align-top text-xs text-muted-foreground" rowSpan={rs}>-</td>}
                      {isColumnVisible("network") && <td className="px-3 py-1.5 align-top text-xs text-muted-foreground" rowSpan={rs}>-</td>}
                    </>
                  )}
                  {isColumnVisible("container") && <td className="px-3 py-1.5 text-xs text-muted-foreground">-</td>}
                  {isColumnVisible("service") && <td className="px-3 py-1.5 text-xs text-muted-foreground whitespace-nowrap">{r.service.replace("http://", "").replace("https://", "").split(":")[0]}</td>}
                  {isColumnVisible("ports") && <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground">{r.service.includes(":") ? r.service.split(":").pop()?.split("/")[0] : "-"}</td>}
                  {isColumnVisible("hostname") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? <input value={draft.hostname} onChange={(e) => updateDraft(draftKey, initial, { hostname: e.target.value })} className={routeInputClass} /> : <a href={`https://${sanitizeHostname(draft.hostname)}`} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline">{sanitizeHostname(draft.hostname)}</a>}
                  </td>}
                  {isColumnVisible("target") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? <ServicePicker value={draft.service} onChange={(service) => updateDraft(draftKey, initial, { service })} /> : <span className="font-mono text-xs text-muted-foreground">{draft.service}</span>}
                  </td>}
                  {isColumnVisible("path") && <td className="px-3 py-1.5 whitespace-nowrap">
                    {isEditing ? <input value={draft.path} onChange={(e) => updateDraft(draftKey, initial, { path: e.target.value })} className={pathInputClass} /> : <span className="font-mono text-xs text-muted-foreground">{draft.path || "-"}</span>}
                  </td>}
                  {isColumnVisible("actions") && (
                    <td className="px-3 py-1.5 align-top whitespace-nowrap">
                      <div className="flex gap-1">
                        {isEditing ? (
                          <Btn onClick={() => saveRoute(draftKey, t.tunnel_id, initial.service, draft)} disabled={isSaving} variant="primary">{isSaving ? "Saving..." : "Save"}</Btn>
                        ) : (
                          <Btn onClick={() => startEditing(draftKey)} variant="ghost">Edit</Btn>
                        )}
                        <Btn onClick={() => deleteRoute(draftKey, t.tunnel_id, initial.service)} disabled={isSaving} variant="danger">Delete Route</Btn>
                      </div>
                    </td>
                  )}
                </tr>
              );});
            })}
          </tbody>
        </table>
      </div>

      <CreateTunnelModal open={showCreate} onClose={() => setShowCreate(false)} />
      <CreateTunnelModal
        open={projectCreate !== null}
        onClose={() => setProjectCreate(null)}
        project={projectCreate?.project.project}
        onCreated={(created) => {
          const routes = projectCreate?.routes ?? [];
          setDraftTunnel({ tunnel_id: created.tunnel_id, name: created.name, project: projectCreate?.project.project ?? "", status: "connected", connections: 0, machine: "local", routes });
        }}
      />
      <ImportTunnelModal open={showImport} onClose={() => setShowImport(false)} />
      {confirmDelete !== null && (
        <DeleteTunnelModal tunnelId={confirmDelete} tunnelName={data.tunnels.find((t) => t.tunnel_id === confirmDelete)?.name ?? ""} onClose={() => setConfirmDelete(null)} onConfirm={(id) => deleteMut.mutate(id)} isPending={deleteMut.isPending} />
      )}
      {confirmRecreate !== null && (
        <Modal open={true} onClose={() => setConfirmRecreate(null)} title="Recreate Tunnel">
          <p className="mb-2 text-sm text-muted-foreground">
            This will delete <span className="font-semibold text-foreground">{data.tunnels.find((t) => t.tunnel_id === confirmRecreate)?.name}</span> and create a fresh tunnel with the same name, routes, and DNS records.
          </p>
          <p className="mb-4 text-sm text-muted-foreground">The sidecar will be respawned on the same network. This is effectively a token rotation — there will be a brief downtime during the switch.</p>
          <div className="flex justify-end gap-2">
            <Btn onClick={() => setConfirmRecreate(null)}>Cancel</Btn>
            <Btn onClick={() => recreateMut.mutate(confirmRecreate)} disabled={recreateMut.isPending} variant="primary">{recreateMut.isPending ? "Recreating..." : "Recreate Tunnel"}</Btn>
          </div>
          {recreateMut.isError && <p className="mt-2 text-xs text-destructive">Recreate failed — check backend logs</p>}
        </Modal>
      )}
    </div>
  );
};

// ---- Zones View ----

const ZonesView = () => {
  const { data, isLoading, error } = useQuery({ queryKey: ["zones"], queryFn: () => apiFetch<ZonesResponse>("/zones") });
  if (isLoading) return <p className="text-muted-foreground">Loading zones...</p>;
  if (error) return <p className="text-destructive">Failed to load zones</p>;
  if (!data) return null;
  return (
    <div className="overflow-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead><tr className="border-b bg-muted text-left text-xs font-medium text-muted-foreground"><th className="px-3 py-2">Zone</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Plan</th><th className="px-3 py-2">Zone ID</th></tr></thead>
        <tbody className="divide-y">{data.zones.map((z) => (
          <tr key={z.zone_id} className="hover:bg-muted/30"><td className="px-3 py-2 font-medium">{z.zone_name}</td><td className="px-3 py-2"><div className="flex items-center gap-1.5"><StatusDot status={z.status} /><span className="text-xs">{z.status}</span></div></td><td className="px-3 py-2 text-xs text-muted-foreground">{z.plan}</td><td className="px-3 py-2 font-mono text-xs text-muted-foreground">{z.zone_id.slice(0, 16)}...</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
};

// ---- App ----

export const App = () => {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => apiFetch<{ status: string }>("/health") });

  useEffect(() => {
    applyTheme(theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="flex items-center gap-4 px-6 py-2">
          <h1 className="text-lg font-bold">Dockflare</h1>
          {health && <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><StatusDot status="running" />connected</span>}
          <nav className="ml-6 flex gap-0.5 rounded-lg bg-muted p-0.5">
            {([["dashboard", "Dashboard"], ["zones", "Zones"]] as const).map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={`rounded px-3 py-1 text-sm font-medium ${tab === k ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>{l}</button>
            ))}
          </nav>
          <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
        </div>
      </header>
      <div className="px-6 py-3">
        {tab === "dashboard" && <DashboardView />}
        {tab === "zones" && <ZonesView />}
      </div>
    </div>
  );
};
