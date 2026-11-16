"use client";

import { Activity, Cctv, Clock, Cpu, Database, RefreshCw, Server, Zap } from "lucide-react";

import { MetricTile } from "@/components/common/metric-tile";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { componentLabel, componentTone, StatusIndicator } from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePolling } from "@/hooks/use-polling";
import { useCallback } from "react";

import { agent } from "@/lib/agent/client";
import { usePermissions } from "@/lib/auth/permissions";
import { api } from "@/lib/api/client";
import { formatDateTime, formatTime, formatUptime } from "@/lib/format";
import { useSystemStatus } from "@/lib/system/system-status-provider";

const COMPONENT_META: Record<string, { label: string; icon: React.ComponentType<{ className?: string }>; note: string }> = {
  api: { label: "API Server", icon: Server, note: "FastAPI · /api/v1" },
  database: { label: "Database", icon: Database, note: "Central database" },
  redis: { label: "Redis", icon: Zap, note: "Optional · server-side cache/queue" },
};

const ROADMAP = [
  { mvp: "01", name: "Core Foundation", state: "done" },
  { mvp: "02", name: "Camera Integration", state: "done" },
  { mvp: "03", name: "Standard Vision Engine", state: "active" },
  { mvp: "04", name: "AI Engine (optional)", state: "planned" },
  { mvp: "05", name: "Edge Runtime", state: "planned" },
  { mvp: "06", name: "Central Server", state: "planned" },
  { mvp: "07", name: "Web Dashboard", state: "planned" },
  { mvp: "08", name: "Analytics & Alerts", state: "planned" },
  { mvp: "09", name: "Production Release", state: "planned" },
] as const satisfies readonly { mvp: string; name: string; state: "done" | "active" | "planned" }[];

export function OverviewView() {
  const status = useSystemStatus();
  const version = usePolling((signal) => api.version(signal), 300_000);
  const agentStatus = usePolling((signal) => agent.status(signal), 10_000);
  const { token } = usePermissions();
  const summaryFetcher = useCallback(
    (signal: AbortSignal) => (token ? agent.eventsSummary(token, signal) : Promise.reject(new Error("Not signed in"))),
    [token],
  );
  const eventSummary = usePolling(summaryFetcher, 15_000);
  const s = status.data;
  const stale = Boolean(status.error && s);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Overview"
        description="Central server health and platform status"
        actions={
          <>
            {s && (
              <Badge variant="outline" className="font-mono text-[10px] uppercase">
                {s.environment}
              </Badge>
            )}
            <Button variant="outline" size="sm" onClick={status.refresh}>
              <RefreshCw /> Refresh
            </Button>
          </>
        }
      />

      {status.error && (
        <Alert variant="destructive">
          <AlertTitle>Central server unreachable</AlertTitle>
          <AlertDescription>
            {stale
              ? `Showing data from ${formatTime(status.updatedAt)}. It may be out of date.`
              : status.error.message}{" "}
            Edge agents are not affected and continue to work locally.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <MetricTile
          label="Cameras online"
          icon={Cctv}
          value={
            agentStatus.data
              ? `${agentStatus.data.cameras_by_status.online ?? 0} / ${agentStatus.data.cameras_total}`
              : agentStatus.loading
                ? <Skeleton className="h-5 w-16" />
                : null
          }
          hint={
            agentStatus.error
              ? "Edge agent unreachable"
              : agentStatus.data
                ? `${agentStatus.data.cameras_by_status.error ?? 0} error · ${(agentStatus.data.cameras_by_status.reconnecting ?? 0) + (agentStatus.data.cameras_by_status.connecting ?? 0)} connecting`
                : "Edge agent"
          }
          className={agentStatus.error && agentStatus.data ? "opacity-60" : undefined}
        />
        <MetricTile label="Edge devices" icon={Cpu} value={null} hint="Edge Runtime arrives in MVP 05" />
        <MetricTile
          label="Motion events today"
          icon={Activity}
          value={eventSummary.data ? (eventSummary.data.counts.motion_started ?? 0) : null}
          hint={
            eventSummary.error
              ? "Edge agent unreachable"
              : eventSummary.data
                ? `${eventSummary.data.counts.line_crossed ?? 0} line crossings · since 00:00 UTC`
                : "Standard Vision Engine"
          }
        />
        <MetricTile
          label="Server uptime"
          icon={Clock}
          value={s ? formatUptime(s.uptime_seconds) : status.loading ? <Skeleton className="h-5 w-16" /> : null}
          hint={s ? `Since ${formatDateTime(s.started_at)}` : "No data"}
          className={stale ? "opacity-60" : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          title="System health"
          icon={Server}
          className="xl:col-span-2"
          bodyClassName="p-0"
          actions={
            s && (
              <StatusIndicator
                tone={stale ? "idle" : componentTone(s.status)}
                label={stale ? "Stale" : componentLabel(s.status)}
              />
            )
          }
        >
          <ul className="divide-y" aria-busy={status.loading}>
            {status.loading && !s
              ? [0, 1, 2].map((i) => (
                  <li key={i} className="flex items-center gap-4 px-4 py-3">
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="ml-auto h-4 w-24" />
                  </li>
                ))
              : Object.entries(s?.components ?? {}).map(([key, c]) => {
                  const meta = COMPONENT_META[key] ?? { label: key, icon: Server, note: "" };
                  const Icon = meta.icon;
                  return (
                    <li key={key} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                      <Icon className="size-4 text-muted-foreground" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{meta.label}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {key === "database" && c.state === "ok" && c.detail
                            ? `${c.detail === "postgresql" ? "PostgreSQL" : c.detail === "sqlite" ? "SQLite (local development)" : c.detail}`
                            : (c.detail ?? meta.note)}
                        </div>
                      </div>
                      <span className="tabular w-16 text-right font-mono text-xs text-muted-foreground">
                        {c.latency_ms != null ? `${c.latency_ms} ms` : "—"}
                      </span>
                      <StatusIndicator
                        className="w-28 justify-end"
                        tone={stale ? "idle" : componentTone(c.state)}
                        label={stale ? "Unknown" : componentLabel(c.state)}
                      />
                    </li>
                  );
                })}
            {!status.loading && !s && (
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">No status data received.</li>
            )}
          </ul>
        </Panel>

        <Panel title="Build" icon={Cpu}>
          {version.data ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">Service</dt>
              <dd className="truncate text-right">{version.data.name}</dd>
              <dt className="text-muted-foreground">Version</dt>
              <dd className="text-right font-mono">{version.data.version}</dd>
              <dt className="text-muted-foreground">API</dt>
              <dd className="text-right font-mono">{version.data.api_version}</dd>
              <dt className="text-muted-foreground">Python</dt>
              <dd className="text-right font-mono">{version.data.python_version}</dd>
            </dl>
          ) : version.error ? (
            <p className="text-sm text-muted-foreground">Version unavailable: {version.error.message}</p>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Platform modules" icon={Activity}>
        <ol className="grid gap-2 sm:grid-cols-3 xl:grid-cols-9">
          {ROADMAP.map((m) => (
            <li
              key={m.mvp}
              className={
                m.state === "active"
                  ? "rounded-md border border-primary/40 bg-primary/10 px-3 py-2"
                  : m.state === "done"
                    ? "rounded-md border px-3 py-2"
                    : "rounded-md border px-3 py-2 opacity-70"
              }
            >
              <div className="font-mono text-[10px] text-muted-foreground">MVP {m.mvp}</div>
              <div className="text-xs font-medium">{m.name}</div>
              <div
                className={
                  m.state === "active"
                    ? "mt-1 text-[10px] text-primary"
                    : m.state === "done"
                      ? "mt-1 text-[10px] text-status-online"
                      : "mt-1 text-[10px] text-muted-foreground"
                }
              >
                {m.state === "active" ? "In progress" : m.state === "done" ? "Done" : "Planned"}
              </div>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}
