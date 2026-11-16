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
import { useLiveMessages } from "@/lib/realtime/use-live-messages";
import type { ServerMessage } from "@/lib/realtime/use-server-events";

import { DeviceSheet } from "./device-sheet";
import { EnrollDialog } from "./enroll-dialog";
import { FleetStatusIndicator, timeAgo } from "./fleet-status";

interface Notice {
  at: string;
  level: string;
  message: string;
}

const pct = (v: number | undefined) =>
  typeof v === "number" ? `${Math.round(v)}%` : "—";

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

  const refresh = list.refresh;
  const known = list.data;
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
        if (!known?.some((d) => d.id === id)) refresh();
      } else if (m.type === "event.created") {
        setEventsLive((n) => n + (Number(m.data.count) || 0));
      } else if (
        m.type === "sync.status" &&
        id &&
        m.data.kind === "configuration"
      ) {
        setRevisions((r) => ({ ...r, [id]: (r[id] ?? 0) + 1 }));
      } else if (m.type === "system.notification") {
        const notice = {
          at: m.at ?? new Date().toISOString(),
          level: String(m.data.level ?? "info"),
          message: String(m.data.message ?? ""),
        };
        setNotices((prev) => [notice, ...prev].slice(0, 6));
      }
    },
    [known, refresh],
  );
  useLiveMessages(
    ["device.status", "event.created", "sync.status", "system.notification"],
    onMessage,
  );

  // Central server unreachable but we have an older list: never show it as current.
  const serverDown = Boolean(list.error);
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
  const show = (v: number) => (list.data && !serverDown ? v : null);

  return (
    <div className="flex flex-col gap-5">
      {serverDown && (
        <Alert variant="destructive">
          <AlertTitle>Central server unreachable</AlertTitle>
          <AlertDescription>
            {list.data
              ? `Device statuses below are the last known state (${formatTime(list.updatedAt)}) and are shown as unknown until the server answers again.`
              : list.error?.message}{" "}
            Edge devices keep working and recording on their own.
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
          value={show((counts.online ?? 0) + (counts.synchronizing ?? 0))}
          hint="incl. synchronizing"
        />
        <MetricTile
          label="Attention"
          value={show((counts.degraded ?? 0) + (counts.error ?? 0))}
          hint="degraded or error"
        />
        <MetricTile
          label="Offline"
          value={show(counts.offline ?? 0)}
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
          canManage && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEnrollOpen(true)}
            >
              <Plus className="size-4" /> Add device
            </Button>
          )
        }
      >
        {list.data && devices.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            No edge devices yet. Use “Add device” to create an enrollment token,
            then register the agent with it.
          </p>
        ) : (
          <Table data-testid="fleet-table">
            <TableHeader>
              <TableRow>
                <TableHead>Device</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">CPU</TableHead>
                <TableHead className="text-right">RAM</TableHead>
                <TableHead className="text-right">GPU</TableHead>
                <TableHead className="text-right">Temp</TableHead>
                <TableHead className="text-right">Disk</TableHead>
                <TableHead>Network</TableHead>
                <TableHead>Cameras</TableHead>
                <TableHead>AI</TableHead>
                <TableHead>Config</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Heartbeat</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.map((d) => {
                const cams = d.camera_status ?? {};
                const hw = d.metrics?.hardware ?? {};
                const net = hw.network;
                return (
                  <TableRow
                    key={d.id}
                    className="cursor-pointer"
                    onClick={() => setSelected(d.id)}
                    data-device={d.name}
                  >
                    <TableCell>
                      <div className="font-medium">{d.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {d.group_name ?? "No group"}
                      </div>
                    </TableCell>
                    <TableCell>
                      {serverDown ? (
                        <StatusIndicator tone="idle" label="Unknown" />
                      ) : (
                        <FleetStatusIndicator status={d.status} />
                      )}
                      {(serverDown ||
                        (d.status_reason && d.status !== "online")) && (
                        <div
                          className="max-w-52 truncate text-xs text-muted-foreground"
                          title={d.status_reason ?? ""}
                        >
                          {serverDown
                            ? `last known: ${d.status}`
                            : d.status_reason}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {pct(hw.cpu?.percent)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {pct(hw.memory?.percent)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {hw.gpu?.available
                        ? pct(hw.gpu.utilization_percent)
                        : "none"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {hw.temperature?.available && hw.temperature.max_c != null
                        ? `${Math.round(hw.temperature.max_c)}°C`
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {pct(hw.disk?.used_percent)}
                    </TableCell>
                    <TableCell className="text-xs">
                      {net
                        ? net.connected
                          ? `${net.interfaces?.length ?? 0} up`
                          : "down"
                        : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {cams.total != null
                        ? `${cams.by_status?.online ?? 0}/${cams.total}`
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {d.ai_status?.enabled
                        ? (d.ai_status.mode ?? "on")
                        : "off"}
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
                      ? "text-status-warning"
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
