"use client";

import {
  Activity,
  BrainCircuit,
  Cctv,
  Clock,
  Cpu,
  Database,
  RefreshCw,
  Server,
  UserRoundSearch,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";

import { MetricTile } from "@/components/common/metric-tile";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import {
  componentLabel,
  componentTone,
  StatusIndicator,
} from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import { api } from "@/lib/api/client";
import { EVENT_LABELS, platform } from "@/lib/api/platform";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime, formatTime, formatUptime } from "@/lib/format";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";
import { useSystemStatus } from "@/lib/system/system-status-provider";

const COMPONENT_META: Record<
  string,
  {
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    note: string;
  }
> = {
  api: { label: "API Server", icon: Server, note: "FastAPI · /api/v1" },
  database: { label: "Database", icon: Database, note: "Central database" },
  redis: {
    label: "Redis",
    icon: Zap,
    note: "Optional · server-side cache/queue",
  },
};

/** Local midnight — "today" is the viewer's day, computed at request time. */
function todayStart(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

export function OverviewView() {
  const status = useSystemStatus();
  const version = usePolling((signal) => api.version(signal), 300_000);
  const agentStatus = usePolling((signal) => agent.status(signal), 10_000);
  const { token } = usePermissions();
  const summaryFetcher = useCallback(
    (signal: AbortSignal) =>
      token
        ? agent.eventsSummary(token, signal)
        : Promise.reject(new Error("Not signed in")),
    [token],
  );
  const eventSummary = usePolling(summaryFetcher, 15_000);
  const overviewFetcher = useCallback(
    (signal: AbortSignal) =>
      token
        ? platform.overview(token, todayStart(), signal)
        : Promise.reject(new Error("Not signed in")),
    [token],
  );
  // Polling is the fallback; real-time messages trigger a debounced refresh in between.
  const overview = usePolling(overviewFetcher, 30_000);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const refreshOverview = overview.refresh;
  useLiveMessages(["event.created", "device.status", "camera.status"], () => {
    clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(refreshOverview, 1500);
  });
  useEffect(() => () => clearTimeout(refreshTimer.current), []);

  const s = status.data;
  const stale = Boolean(status.error && s);
  const o = overview.data;
  const fleetStale = Boolean(overview.error && o);
  const local = agentStatus.data;
  const localOnly = !o && Boolean(local);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Overview"
        description="All edge devices and cameras at a glance. Counters update in real time."
        actions={
          <>
            {s && (
              <Badge
                variant="outline"
                className="font-mono text-[10px] uppercase"
              >
                {s.environment}
              </Badge>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                status.refresh();
                overview.refresh();
              }}
            >
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
      {localOnly && local && (
        <Alert>
          <AlertTitle>Fleet data unavailable</AlertTitle>
          <AlertDescription>
            Counters below are for the edge agent this dashboard is connected to
            ({local.cameras_total} cameras), not the whole fleet. Device
            counters stay empty instead of showing unconfirmed values.
          </AlertDescription>
        </Alert>
      )}

      <div
        data-testid="overview-counters"
        className={`grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5 ${fleetStale ? "opacity-60" : ""}`}
      >
        <MetricTile
          label="Cameras"
          icon={Cctv}
          value={
            o?.cameras
              ? o.cameras.total
              : localOnly && local
                ? local.cameras_total
                : null
          }
          hint={
            o?.cameras
              ? "all edge devices"
              : localOnly
                ? "this agent only"
                : undefined
          }
        />
        <MetricTile
          label="Cameras online"
          value={
            o?.cameras
              ? o.cameras.online
              : localOnly && local
                ? (local.cameras_by_status.online ?? 0)
                : null
          }
          hint={fleetStale ? "last known" : "frames arriving"}
        />
        <MetricTile
          label="Cameras offline"
          value={
            o?.cameras
              ? o.cameras.offline
              : localOnly && local
                ? local.cameras_total - (local.cameras_by_status.online ?? 0)
                : null
          }
          hint="error, reconnecting or off"
        />
        <MetricTile
          label="Edge devices"
          icon={Cpu}
          value={o?.devices ? o.devices.total : null}
          hint="registered"
        />
        <MetricTile
          label="Active devices"
          value={o?.devices ? o.devices.active : null}
          hint={
            o?.devices
              ? `${o.devices.by_status.offline ?? 0} offline`
              : undefined
          }
        />
        <MetricTile
          label="Motion events today"
          icon={Activity}
          value={
            o?.events_today
              ? o.events_today.motion
              : localOnly && eventSummary.data
                ? (eventSummary.data.counts.motion_started ?? 0)
                : null
          }
          hint={
            o?.events_today
              ? `${o.events_today.zone} zone · ${o.events_today.line} line`
              : "Standard Vision Engine"
          }
        />
        <MetricTile
          label="People observations today"
          icon={UserRoundSearch}
          value={o ? o.observations_today : null}
          hint={
            o && o.observations_today === null
              ? "no permission"
              : "AI · anonymous tracks"
          }
        />
        <MetricTile
          label="AI active devices"
          icon={BrainCircuit}
          value={o?.devices ? o.devices.ai_active : null}
          hint={
            o?.devices?.ai_not_running
              ? `${o.devices.ai_not_running} enabled, not running`
              : "running detection"
          }
        />
        <MetricTile
          label="Devices without AI"
          value={o?.devices ? o.devices.without_ai : null}
          hint="Standard Engine only"
        />
        <MetricTile
          label="Server uptime"
          icon={Clock}
          value={
            s ? (
              formatUptime(s.uptime_seconds)
            ) : status.loading ? (
              <Skeleton className="h-5 w-16" />
            ) : null
          }
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
                  const meta = COMPONENT_META[key] ?? {
                    label: key,
                    icon: Server,
                    note: "",
                  };
                  const Icon = meta.icon;
                  return (
                    <li
                      key={key}
                      className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3"
                    >
                      <Icon
                        className="size-4 text-muted-foreground"
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{meta.label}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {key === "database" && c.state === "ok" && c.detail
                            ? c.detail === "postgresql"
                              ? "PostgreSQL"
                              : c.detail === "sqlite"
                                ? "SQLite (local development)"
                                : c.detail
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
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">
                No status data received.
              </li>
            )}
            {local && (
              <li className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                <Cpu className="size-4 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    Edge agent (this site)
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    v{local.version} · server link {local.sync?.state ?? "—"}
                  </div>
                </div>
                <StatusIndicator
                  className="w-28 justify-end"
                  tone={componentTone(local.status)}
                  label={componentLabel(local.status)}
                />
              </li>
            )}
          </ul>
        </Panel>

        <Panel
          title="Recent events"
          icon={Activity}
          bodyClassName="p-0"
          actions={
            <Button asChild variant="ghost" size="sm">
              <Link href="/events">All events</Link>
            </Button>
          }
        >
          {o?.recent_events?.length ? (
            <ul className="divide-y" data-testid="recent-events">
              {o.recent_events.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center gap-3 px-4 py-2 text-sm"
                >
                  <span className="w-16 font-mono text-xs text-muted-foreground">
                    {formatTime(e.occurred_at)}
                  </span>
                  <span className="flex-1 truncate">
                    {EVENT_LABELS[e.event_type] ?? e.event_type}
                  </span>
                  <span className="max-w-32 truncate text-xs text-muted-foreground">
                    {e.camera_name ?? e.device_name ?? ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-4 text-sm text-muted-foreground">
              {o?.recent_events === null
                ? "You don't have permission to see events."
                : o
                  ? "No events yet."
                  : "Central server unavailable."}
            </p>
          )}
        </Panel>
      </div>

      <Panel title="Build" icon={Cpu}>
        {version.data ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Service</dt>
              <dd className="truncate">{version.data.name}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Version</dt>
              <dd className="font-mono">{version.data.version}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">API</dt>
              <dd className="font-mono">{version.data.api_version}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Python</dt>
              <dd className="font-mono">{version.data.python_version}</dd>
            </div>
          </dl>
        ) : version.error ? (
          <p className="text-sm text-muted-foreground">
            Version unavailable: {version.error.message}
          </p>
        ) : (
          <Skeleton className="h-4 w-full" />
        )}
      </Panel>
    </div>
  );
}
