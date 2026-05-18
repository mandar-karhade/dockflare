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
  });

  beforeEach(() => {
    mockApiFetch.mockReset();
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
    expect(within(row as HTMLTableRowElement).getByText("Export")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Recreate")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).getByText("Delete")).toBeInTheDocument();
  });

  test("shows project-scoped create action when a local project has no tunnel", async () => {
    renderApp();

    const row = await screen.findByText("beta").then((node) => node.closest("tr"));

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).getByText("Create New")).toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Edit")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Export")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Recreate")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Delete")).not.toBeInTheDocument();
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
    expect(await screen.findByText("Editing: beta-tunnel")).toBeInTheDocument();
    expect(screen.getByDisplayValue("http://api:3000")).toBeInTheDocument();
    expect(screen.getAllByDisplayValue("").length).toBeGreaterThan(0);
  });

  test("hides row actions for tunnels that do not belong to a local project", async () => {
    renderApp();

    const row = await screen.findByText("orphan-tunnel").then((node) => node.closest("tr"));

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).queryByText("Edit")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Export")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Recreate")).not.toBeInTheDocument();
    expect(within(row as HTMLTableRowElement).queryByText("Delete")).not.toBeInTheDocument();
  });
});
