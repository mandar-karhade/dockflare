import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { App } from "./App";
import { apiFetch } from "./api/client";

vi.mock("./api/client", () => ({
  apiFetch: vi.fn(),
}));

const mockApiFetch = vi.mocked(apiFetch);

const installLocalStorage = () => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
      clear: vi.fn(() => values.clear()),
    },
  });
};

const installSessionStorage = () => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "sessionStorage", {
    configurable: true,
    value: {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
      clear: vi.fn(() => values.clear()),
    },
  });
};

const renderApp = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
};

const dashboard = {
  projects: [
    {
      project: "alpha",
      networks: ["alpha_default"],
      containers: [
        {
          container_id: "alpha-web",
          name: "alpha-web-1",
          service: "web",
          image: "nginx",
          status: "running",
          ports: [8080],
          networks: ["alpha_default"],
          is_cloudflared: false,
          hostname: "alpha.example.com",
          target_service_url: "http://web:8080",
          tunnel_name: "alpha-tunnel",
          tunnel_id: "tun-alpha",
        },
      ],
      tunnel: {
        tunnel_id: "tun-alpha",
        name: "alpha-tunnel",
        status: "connected",
        connections: 1,
        machine: "local",
        route_count: 1,
      },
    },
    {
      project: "beta",
      networks: ["beta_default"],
      containers: [
        {
          container_id: "beta-api",
          name: "beta-api-1",
          service: "api",
          image: "node",
          status: "running",
          ports: [3000],
          networks: ["beta_default"],
          is_cloudflared: false,
          hostname: null,
          target_service_url: null,
          tunnel_name: null,
          tunnel_id: null,
        },
      ],
      tunnel: null,
    },
  ],
  standalone: [],
  tunnels: [
    {
      tunnel_id: "tun-alpha",
      name: "alpha-tunnel",
      status: "connected",
      connections: 1,
      is_local: true,
      machine: "local",
      origin_ip: "127.0.0.1",
      routes: [{ hostname: "alpha.example.com", service: "http://web:8080", path: null }],
      sidecar: { name: "cftunnel-alpha", project: "alpha", networks: ["alpha_default"], status: "running" },
    },
    {
      tunnel_id: "tun-orphan",
      name: "orphan-tunnel",
      status: "disconnected",
      connections: 0,
      is_local: false,
      machine: "remote",
      origin_ip: null,
      routes: [{ hostname: "orphan.example.com", service: "http://ghost:8080", path: null }],
      sidecar: null,
    },
  ],
  machines: { local: 1, remote: 1 },
  local_ip: "127.0.0.1",
  total_tunnels: 2,
  total_projects: 2,
};

describe("dashboard actions", () => {
  afterEach(() => {
    cleanup();
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
  });

  beforeEach(() => {
    mockApiFetch.mockReset();
    installLocalStorage();
    installSessionStorage();
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/containers") {
        return {
          projects: {
            beta: [
              {
                compose_service: "api",
                name: "beta-api-1",
                exposed_ports: [3000],
                status: "running",
                is_cloudflared: false,
              },
            ],
          },
        };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
  });

  test("shows existing tunnel actions for a project that already has a tunnel", async () => {
    renderApp();

    const row = await screen.findByText("alpha-tunnel").then((node) => node.closest("tr"));

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).getByText("Edit")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Delete Route")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Export")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Refresh")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Recreate")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Delete Tunnel")).toBeInTheDocument();
  });

  test("shows project create once in the tunnel column for multiple services", async () => {
    const betaProject = dashboard.projects[1]!;
    const betaContainer = betaProject.containers[0]!;
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") {
        return {
          ...dashboard,
          projects: [
            {
              ...betaProject,
              containers: [
                betaContainer,
                { ...betaContainer, container_id: "beta-worker", name: "beta-worker-1", service: "worker" },
              ],
            },
          ],
          tunnels: [],
        };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    await screen.findByText("beta");

    expect(screen.getAllByText("Create New")).toHaveLength(1);
  });

  test("shows project-scoped create action when a local project has no tunnel", async () => {
    renderApp();

    const row = await screen.findByText("beta").then((node) => node.closest("tr"));

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).getByText("Create New")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Edit")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Export")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Refresh")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Recreate")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Delete Tunnel")).not.toBeInTheDocument();
  });

  test("project-scoped create asks only for a name and seeds route targets after creation", async () => {
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/containers") {
        return {
          projects: {
            beta: [
              {
                compose_service: "api",
                name: "beta-api-1",
                exposed_ports: [3000],
                status: "running",
                is_cloudflared: false,
              },
            ],
          },
        };
      }
      if (path === "/tunnels" && options?.method === "POST") {
        return { tunnel_id: "tun-beta", name: "beta-tunnel", status: "created" };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    const row = await screen.findByText("beta").then((node) => node.closest("tr"));
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Create New"));

    expect(screen.getByText("Create New Tunnel")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.queryByLabelText("Compose Project")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Service")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "beta-tunnel" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/tunnels",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ name: "beta-tunnel", primary_compose_project: "beta" }),
        }),
      );
    });
    await screen.findByText("beta-tunnel");
    expect(screen.queryByText("Editing: beta-tunnel")).not.toBeInTheDocument();
    expect(screen.getByText("http://api:3000")).toBeInTheDocument();
  });

  test("saves hostname edits inline with sanitized hostname and service URL target", async () => {
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/tunnels/tun-alpha/ingress" && options?.method === "PUT") {
        return { status: "updated" };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    const row = await screen.findByText("alpha-web-1").then((node) => node.closest("tr"));
    expect(within(row as HTMLTableRowElement).queryByDisplayValue("alpha.example.com")).not.toBeInTheDocument();
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Edit"));
    const hostname = within(row as HTMLTableRowElement).getByDisplayValue("alpha.example.com");
    fireEvent.change(hostname, { target: { value: "https://app-stage.anywebalert.com" } });
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Save"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/tunnels/tun-alpha/ingress",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify([
            { hostname: "app-stage.anywebalert.com", service: "http://web:8080", path: null },
          ]),
        }),
      );
    });
  });

  test("shows saving state only on the row being saved", async () => {
    const alphaProject = dashboard.projects[0]!;
    const alphaContainer = alphaProject.containers[0];
    const twoRouteDashboard = {
      ...dashboard,
      projects: [
        {
          ...alphaProject,
          containers: [
            alphaContainer,
            {
              ...alphaContainer,
              container_id: "alpha-api",
              name: "alpha-api-1",
              service: "api",
              ports: [3000],
              hostname: "api.example.com",
              target_service_url: "http://api:3000",
            },
          ],
        },
      ],
      tunnels: [
        {
          ...dashboard.tunnels[0],
          routes: [
            { hostname: "alpha.example.com", service: "http://web:8080", path: null },
            { hostname: "api.example.com", service: "http://api:3000", path: null },
          ],
        },
      ],
    };
    let resolveSave: (value: unknown) => void = () => {};
    mockApiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/health") return Promise.resolve({ status: "ok" });
      if (path === "/dashboard") return Promise.resolve(twoRouteDashboard);
      if (path === "/tunnels/tun-alpha/ingress" && options?.method === "PUT") {
        return new Promise((resolve) => { resolveSave = resolve; });
      }
      return Promise.reject(new Error(`Unhandled API path: ${path}`));
    });
    renderApp();

    const webRow = await screen.findByText("alpha-web-1").then((node) => node.closest("tr"));
    const apiRow = await screen.findByText("alpha-api-1").then((node) => node.closest("tr"));
    fireEvent.click(within(webRow as HTMLTableRowElement).getByText("Edit"));
    fireEvent.click(within(webRow as HTMLTableRowElement).getByText("Save"));

    expect(within(webRow as HTMLTableRowElement).getByText("Saving...")).toBeInTheDocument();
    expect(within(apiRow as HTMLTableRowElement).getByText("Edit")).toBeInTheDocument();
    expect(within(apiRow as HTMLTableRowElement).queryByDisplayValue("api.example.com")).not.toBeInTheDocument();

    resolveSave({ status: "updated" });
  });

  test("deletes a route inline by saving the tunnel ingress without that target", async () => {
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/tunnels/tun-alpha/ingress" && options?.method === "PUT") {
        return { status: "updated" };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    const row = await screen.findByText("alpha-web-1").then((node) => node.closest("tr"));
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Delete Route"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/tunnels/tun-alpha/ingress",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify([]),
        }),
      );
    });
  });

  test("saves the service URL when picking a target from the dropdown", async () => {
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/containers") {
        return {
          projects: {
            beta: [
              {
                compose_service: "api",
                name: "beta-api-1",
                exposed_ports: [3000],
                status: "running",
                is_cloudflared: false,
              },
            ],
          },
        };
      }
      if (path === "/tunnels/tun-alpha/ingress" && options?.method === "PUT") {
        return { status: "updated" };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    const row = await screen.findByText("alpha-web-1").then((node) => node.closest("tr"));
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Edit"));
    fireEvent.focus(within(row as HTMLTableRowElement).getByDisplayValue("http://web:8080"));
    fireEvent.click(await screen.findByRole("button", { name: /api\s+http:\/\/api:3000/ }));
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Save"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/tunnels/tun-alpha/ingress",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify([
            { hostname: "alpha.example.com", service: "http://api:3000", path: null },
          ]),
        }),
      );
    });
  });

  test("allows editing one orphan route row at a time", async () => {
    renderApp();

    const row = await screen.findByText("orphan-tunnel").then((node) => node.closest("tr"));

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).queryByDisplayValue("orphan.example.com")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Edit")).toBeInTheDocument();
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Edit"));
    expect(within(row as HTMLTableRowElement).getByDisplayValue("orphan.example.com")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByDisplayValue("http://ghost:8080")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Save")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Delete Route")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Export")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Refresh")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Recreate")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Delete Tunnel")).not.toBeInTheDocument();
  });

  test("refresh action refreshes only the selected tunnel", async () => {
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/health") return { status: "ok" };
      if (path === "/dashboard") return dashboard;
      if (path === "/tunnels/tun-alpha/refresh" && options?.method === "POST") {
        return { tunnel_id: "tun-alpha", status: "connected", connections: 1 };
      }
      throw new Error(`Unhandled API path: ${path}`);
    });
    renderApp();

    const row = await screen.findByText("alpha-tunnel").then((node) => node.closest("tr"));
    fireEvent.click(within(row as HTMLTableRowElement).getByText("Refresh"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/tunnels/tun-alpha/refresh",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  test("defaults to dark theme and toggles to light", async () => {
    renderApp();

    expect(await screen.findByLabelText("Switch to light theme")).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem("dockflare-theme")).toBe("dark");

    fireEvent.click(screen.getByLabelText("Switch to light theme"));

    expect(screen.getByLabelText("Switch to dark theme")).toBeInTheDocument();
    expect(document.documentElement).not.toHaveClass("dark");
    expect(window.localStorage.getItem("dockflare-theme")).toBe("light");
  });

  test("hides dashboard columns for the session and resets the view", async () => {
    renderApp();

    expect(await screen.findByRole("columnheader", { name: "Target" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Columns"));
    fireEvent.click(screen.getByLabelText("Target"));

    expect(screen.queryByRole("columnheader", { name: "Target" })).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("dockflare-dashboard-columns")).toContain('"target":false');

    cleanup();
    renderApp();

    await screen.findByText("alpha-tunnel");
    expect(screen.queryByRole("columnheader", { name: "Target" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Columns"));
    fireEvent.click(screen.getByText("Reset View"));

    expect(screen.getByRole("columnheader", { name: "Target" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("dockflare-dashboard-columns")).toContain('"target":true');
  });
});
