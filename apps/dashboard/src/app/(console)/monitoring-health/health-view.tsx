"use client";

import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiRequest, ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";
import { cn } from "@/lib/utils";

type State = "ok" | "warning" | "degraded" | "error" | "not_configured";

interface Component {
  state: State;
  [k: string]: unknown;
}

interface Snapshot {
  status: State;
  generated_at: string;
  components: Record<string, Component>;
}

const LABELS: Record<string, string> = {
  backend: "Backend",
  database: "Database",
  redis: "Redis",
  disk: "Server disk",
  edge_devices: "Edge devices",
  cameras: "Cameras",
  ai: "AI engines",
  synchronization: "Synchronization",
};

/** Status colour is never alone: icon + word always accompany it. */
function StateBadge({ state }: { state: State }) {
  const map = {
    ok: { icon: CheckCircle2, label: "OK", cls: "text-[var(--status-online)]" },
    warning: {
      icon: AlertTriangle,
      label: "Warning",
      cls: "text-[var(--status-warning)]",
    },
    degraded: {
      icon: AlertTriangle,
      label: "Degraded",
      cls: "text-[var(--status-warning)]",
    },
    error: {
      icon: AlertOctagon,
      label: "Error",
      cls: "text-[var(--status-error)]",
    },
    not_configured: {
      icon: CircleDashed,
      label: "Not configured",
      cls: "text-muted-foreground",
    },
  }[state] ?? {
    icon: CircleDashed,
    label: state,
    cls: "text-muted-foreground",
  };
  const Icon = map.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs font-medium",
        map.cls,
      )}
    >
      <Icon className="size-3.5" aria-hidden /> {map.label}
    </span>
  );
}

function summary(key: string, c: Component): string {
  const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : "—");
  const counts = (o: unknown) =>
    Object.entries((o as Record<string, number>) ?? {})
      .map(([k, v]) => `${v} ${k}`)
      .join(", ") || "none";
  switch (key) {
    case "backend":
      return `up ${Math.round(Number(c.uptime_s ?? 0) / 60)} min`;
    case "database":
      return c.latency_ms != null
        ? `${n(c.latency_ms)} ms`
        : String(c.detail ?? "");
    case "redis":
      return String(c.detail ?? "");
    case "disk":
      return `${n(c.free_gb)} GB free · ${n(c.used_percent)} % used`;
    case "edge_devices":
      return `${n(c.total)} total · ${counts(c.by_status)}`;
    case "cameras":
      return `${n(c.total)} enabled · ${counts(c.by_status)}`;
    case "ai":
      return counts(c.by_mode);
    case "synchronization": {
      const rows = (c.devices_with_backlog as unknown[]) ?? [];
      return rows.length
        ? `${rows.length} device(s) with an upload backlog`
        : "no backlog";
    }
    default:
      return "";
  }
}

export function HealthView() {
  const { token, can } = usePermissions();
  const allowed = can("devices:read");
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const load = useCallback(
    async (signal: AbortSignal) => {
      if (!token || !allowed) return;
      try {
        setData(
          await apiRequest<Snapshot>("/system/monitoring", { token, signal }),
        );
        setError(null);
      } catch (e) {
        if (!signal.aborted)
          setError(
            e instanceof ApiError
              ? e.message
              : "Central server is unreachable.",
          );
      }
    },
    [token, allowed],
  );

  useEffect(() => {
    const c = new AbortController();
    void Promise.resolve().then(() => load(c.signal));
    return () => c.abort();
  }, [load, tick]);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 15_000);
    return () => clearInterval(id);
  }, []);
  useLiveMessages(["device.status", "camera.status"], () =>
    setTick((t) => t + 1),
  );

  if (!allowed)
    return (
      <Alert variant="destructive">
        <AlertTitle>No access</AlertTitle>
        <AlertDescription>
          Your role doesn&apos;t include devices:read.
        </AlertDescription>
      </Alert>
    );

  const c = data?.components;
  const devices = (c?.edge_devices?.devices as Record<string, unknown>[]) ?? [];
  const problems = [
    ...((c?.cameras?.problems as Record<string, unknown>[]) ?? []).map(
      (p) => `Camera ${p.name}: ${p.status}${p.error ? ` — ${p.error}` : ""}`,
    ),
    ...((c?.ai?.problems as Record<string, unknown>[]) ?? []).map(
      (p) => `AI on ${p.device}: ${p.mode}${p.reason ? ` — ${p.reason}` : ""}`,
    ),
    ...(
      (c?.synchronization?.devices_with_backlog as Record<string, unknown>[]) ??
      []
    ).map(
      (p) =>
        `${p.device}: ${p.pending ?? 0} events waiting for upload${p.dropped ? `, ${p.dropped} dropped` : ""}`,
    ),
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="System health"
        description="Every component at a glance — server, database, devices, cameras, AI, synchronization."
      />
      <div className="flex items-center gap-3 rounded-lg border bg-card p-2 text-sm">
        {data && (
          <>
            Overall <StateBadge state={data.status} />
            <span className="text-xs text-muted-foreground">
              updated {formatDateTime(data.generated_at)}
            </span>
          </>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => setTick((t) => t + 1)}
        >
          <RefreshCw /> Refresh
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Central server unreachable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
        data-testid="health-components"
      >
        {!c
          ? Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-20" />
            ))
          : Object.keys(LABELS).map((k) =>
              c[k] ? (
                <div
                  key={k}
                  className="flex flex-col gap-1 rounded-lg border bg-card px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-medium tracking-[0.06em] text-muted-foreground uppercase">
                      {LABELS[k]}
                    </span>
                    <StateBadge state={c[k].state} />
                  </div>
                  <div className="truncate text-sm">{summary(k, c[k])}</div>
                </div>
              ) : null,
            )}
      </div>
      {problems.length > 0 && (
        <Panel title="Needs attention">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Panel>
      )}
      {devices.length > 0 && (
        <Panel title="Edge devices" bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  {[
                    "Device",
                    "Status",
                    "Last heartbeat",
                    "CPU",
                    "RAM",
                    "Disk free",
                    "Temp.",
                  ].map((h, i) => (
                    <th
                      key={h}
                      className={cn(
                        "px-3 py-2 font-medium",
                        i > 2 ? "text-right" : "text-left",
                      )}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={String(d.id)} className="border-t">
                    <td className="px-3 py-2">{String(d.name)}</td>
                    <td className="px-3 py-2">
                      {String(d.status)}
                      {d.stale ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          (stale)
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {d.last_heartbeat_at
                        ? formatDateTime(String(d.last_heartbeat_at))
                        : "—"}
                    </td>
                    {(
                      [
                        "cpu_percent",
                        "memory_percent",
                        "disk_free_gb",
                        "temperature_c",
                      ] as const
                    ).map((k) => (
                      <td
                        key={k}
                        className="px-3 py-2 text-right font-mono tabular-nums"
                      >
                        {d[k] == null
                          ? "—"
                          : `${Number(d[k]).toFixed(k === "disk_free_gb" ? 1 : 0)}${k === "disk_free_gb" ? " GB" : k === "temperature_c" ? " °C" : " %"}`}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
