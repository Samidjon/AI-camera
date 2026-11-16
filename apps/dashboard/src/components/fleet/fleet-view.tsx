"use client";

import { Bell, Plus, Radio, Server } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { MetricTile } from "@/components/common/metric-tile";
import { Panel } from "@/components/common/panel";
import { StatusIndicator } from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePolling } from "@/hooks/use-polling";
import { fleet, type FleetDevice } from "@/lib/api/fleet";
import { usePermissions } from "@/lib/auth/permissions";
import { formatTime } from "@/lib/format";
import {
  useServerEvents,
  type ServerMessage,
} from "@/lib/realtime/use-server-events";

import { DeviceSheet } from "./device-sheet";
import { EnrollDialog } from "./enroll-dialog";
import { FleetStatusIndicator, timeAgo } from "./fleet-status";

interface Notice {
  at: string;
  level: string;
  message: string;
}

/** All edge devices registered with the central server, updated live over WebSocket. */
export function FleetView() {
  const { token, can } = usePermissions();
  const canManage = can("devices:manage");
  const fetcher = useCallback(
    (signal: AbortSignal) =>
      token
        ? fleet.devices(token, signal)
        : Promise.reject(new Error("Not signed in")),
    [token],
  );
  // Polling is the fallback; live changes arrive over the WebSocket in between.
  const list = usePolling(fetcher, 30_000);
  const [live, setLive] = useState<Record<string, Partial<FleetDevice>>>({});
  const liveRef = useRef(live);
  const [revisions, setRevisions] = useState<Record<string, number>>({});
  const [notices, setNotices] = useState<Notice[]>([]);
  const [eventsLive, setEventsLive] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(t);
  }, []);

  const onMessage = useCallback(
    (m: ServerMessage) => {
      const id =
        typeof m.data.device_id === "string"
          ? m.data.device_id
          : typeof m.data.id === "string"
            ? m.data.id
            : null;
      if (m.type === "device.status" && id) {
        const data = m.data as Partial<FleetDevice>;
        const before = liveRef.current[id];
        if (
          before?.desired_config_version !== data.desired_config_version ||
          before?.applied_config_version !== data.applied_config_version
        ) {
          setRevisions((r) => ({ ...r, [id]: (r[id] ?? 0) + 1 }));
        }
        liveRef.current = { ...liveRef.current, [id]: data };
        setLive(liveRef.current);
        if (!list.data?.some((d) => d.id === id)) list.refresh();
      } else if (m.type === "event.created") {
        setEventsLive((n) => n + (Number(m.data.count) || 0));
      } else if (
        m.type === "sync.status" &&
        id &&
        m.data.kind === "configuration"
      ) {
        setRevisions((r) => ({ ...r, [id]: (r[id] ?? 0) + 1 }));
      } else if (m.type === "system.notification") {
        setNotices((prev) =>
          [
            {
              at: m.at ?? new Date().toISOString(),
              level: String(m.data.level ?? "info"),
              message: String(m.data.message ?? ""),
            },
            ...prev,
          ].slice(0, 6),
        );
      }
    },
    [list],
  );
  const liveState = useServerEvents(token, onMessage);

  const devices = useMemo(
    () =>
      (list.data ?? []).map(
        (d) => ({ ...d, ...(live[d.id] ?? {}) }) as FleetDevice,
      ),
    [list.data, live],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const d of devices) c[d.status] = (c[d.status] ?? 0) + 1;
    return c;
  }, [devices]);

  return (
    <div className="flex flex-col gap-5">
      {list.error && !list.data && (
        <Alert variant="destructive">
          <AlertTitle>Central server unreachable</AlertTitle>
          <AlertDescription>
            {list.error.message}. Edge devices keep working on their own; this
            view needs the central server.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <MetricTile
          label="Devices"
          icon={Server}
          value={list.data ? devices.length : null}
        />
        <MetricTile
          label="Online"
          value={
            list.data
              ? (counts.online ?? 0) + (counts.synchronizing ?? 0)
              : null
          }
          hint="incl. synchronizing"
        />
        <MetricTile
          label="Attention"
          value={
            list.data ? (counts.degraded ?? 0) + (counts.error ?? 0) : null
          }
          hint="degraded or error"
        />
        <MetricTile
          label="Offline"
          value={list.data ? (counts.offline ?? 0) : null}
          hint={counts.revoked ? `${counts.revoked} revoked` : undefined}
        />
        <MetricTile
          label="Events (live)"
          icon={Radio}
          value={eventsLive}
          hint="since this page opened"
        />
      </div>

      <Panel
        title="Edge devices"
        icon={Server}
        bodyClassName="p-0"
        actions={
          <div className="flex items-center gap-3">
            <StatusIndicator
              tone={
                liveState === "live"
                  ? "online"
                  : liveState === "connecting"
                    ? "warning"
                    : "idle"
              }
              label={
                liveState === "live"
                  ? "Live"
                  : liveState === "connecting"
                    ? "Connecting"
                    : "Live updates off"
              }
              pulse={liveState === "live"}
            />
            {canManage && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEnrollOpen(true)}
              >
                <Plus className="size-4" /> Add device
              </Button>
            )}
          </div>
        }
      >
        {list.data && devices.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            No edge devices yet. Use “Add device” to create an enrollment token,
            then register the agent with it.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Device</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Cameras</TableHead>
                <TableHead>AI</TableHead>
                <TableHead className="text-right">Queue</TableHead>
                <TableHead>Config</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Heartbeat</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.map((d) => {
                const cams = d.camera_status ?? {};
                const online = cams.by_status?.online ?? 0;
                return (
                  <TableRow
                    key={d.id}
                    className="cursor-pointer"
                    onClick={() => setSelected(d.id)}
                  >
                    <TableCell>
                      <div className="font-medium">{d.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {d.group_name ?? "No group"}
                      </div>
                    </TableCell>
                    <TableCell>
                      <FleetStatusIndicator status={d.status} />
                      {d.status_reason && d.status !== "online" && (
                        <div
                          className="max-w-56 truncate text-xs text-muted-foreground"
                          title={d.status_reason}
                        >
                          {d.status_reason}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {cams.total != null ? `${online}/${cams.total}` : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {d.ai_status?.enabled
                        ? (d.ai_status.mode ?? "on")
                        : "off"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {d.sync_status?.pending ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {d.desired_config_version === 0
                        ? "local"
                        : d.applied_config_version >= d.desired_config_version
                          ? `v${d.applied_config_version}`
                          : `v${d.applied_config_version} → v${d.desired_config_version}`}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {d.agent_version ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {timeAgo(d.last_heartbeat_at, now)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Panel>

      {notices.length > 0 && (
        <Panel title="Notifications" icon={Bell}>
          {notices.map((n, i) => (
            <div
              key={`${n.at}-${i}`}
              className="flex gap-3 border-b py-1.5 text-sm last:border-0"
            >
              <span className="w-20 font-mono text-xs text-muted-foreground">
                {formatTime(n.at)}
              </span>
              <span
                className={
                  n.level === "error"
                    ? "text-destructive"
                    : n.level === "warning"
                      ? "text-amber-600 dark:text-amber-400"
                      : ""
                }
              >
                {n.message}
              </span>
            </div>
          ))}
        </Panel>
      )}

      {token && (
        <DeviceSheet
          token={token}
          deviceId={selected}
          canManage={canManage}
          revision={selected ? (revisions[selected] ?? 0) : 0}
          onOpenChange={(o) => !o && setSelected(null)}
          onChanged={list.refresh}
        />
      )}
      {token && (
        <EnrollDialog
          token={token}
          open={enrollOpen}
          onOpenChange={setEnrollOpen}
        />
      )}
    </div>
  );
}
