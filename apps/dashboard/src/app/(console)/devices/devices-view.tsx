"use client";

import {
  Archive,
  Cpu,
  Gauge,
  HardDrive,
  Link2,
  Loader2,
  MemoryStick,
  Network,
  RefreshCw,
  ScrollText,
  Server,
  ShieldCheck,
  Thermometer,
  Timer,
  Unlink,
} from "lucide-react";
import { useCallback, useState } from "react";

import { MetricTile } from "@/components/common/metric-tile";
import { Panel } from "@/components/common/panel";
import {
  StatusIndicator,
  type StatusTone,
} from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { DeviceInfo, SyncState } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime, formatUptime } from "@/lib/format";

const SYNC: Record<SyncState, { label: string; tone: StatusTone }> = {
  unconfigured: { label: "Standalone", tone: "idle" },
  registering: { label: "Registering", tone: "warning" },
  online: { label: "Online", tone: "online" },
  offline: { label: "Server unreachable", tone: "warning" },
  revoked: { label: "Revoked", tone: "error" },
  error: { label: "Error", tone: "error" },
};

const LEVELS = ["INFO", "WARNING", "ERROR"] as const;

function err(e: unknown) {
  return e instanceof ApiError ? e.message : "Edge agent is unreachable.";
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono text-xs break-all">
        {children ?? "—"}
      </span>
    </div>
  );
}

export function DevicesView() {
  const { token, can } = usePermissions();
  const canManage = can("devices:manage");
  const deviceFetcher = useCallback(
    (signal: AbortSignal) =>
      token
        ? agent.device(token, signal)
        : Promise.reject(new Error("Not signed in")),
    [token],
  );
  const [level, setLevel] = useState<string>("INFO");
  const logsFetcher = useCallback(
    (signal: AbortSignal) =>
      token
        ? agent.deviceLogs(token, level, signal)
        : Promise.reject(new Error("Not signed in")),
    [token, level],
  );
  const device = usePolling(deviceFetcher, 3_000);
  const logs = usePolling(logsFetcher, 5_000);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [serverUrl, setServerUrl] = useState("");
  const [enrollment, setEnrollment] = useState("");
  const [name, setName] = useState<string | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage({ ok: true, text: ok });
      device.refresh();
      logs.refresh();
    } catch (e) {
      setMessage({ ok: false, text: err(e) });
    } finally {
      setBusy(null);
    }
  }

  const d: DeviceInfo | null = device.data ?? null;
  const sync = d?.sync;
  const syncMeta = sync ? SYNC[sync.state] : null;
  const m = d?.metrics;
  const linked = Boolean(sync?.link?.registered);

  return (
    <div className="flex flex-col gap-5">
      {device.error && (
        <Alert variant="destructive">
          <AlertTitle>Edge agent unreachable</AlertTitle>
          <AlertDescription>{device.error.message}</AlertDescription>
        </Alert>
      )}
      {message && (
        <Alert variant={message.ok ? "default" : "destructive"}>
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}

      {d && m && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <MetricTile
              label="CPU"
              icon={Cpu}
              value={`${Math.round(m.cpu.percent)}%`}
              hint={`${m.cpu.cores ?? "?"} cores`}
            />
            <MetricTile
              label="Memory"
              icon={MemoryStick}
              value={`${Math.round(m.memory.percent)}%`}
              hint={`${m.memory.used_mb} / ${m.memory.total_mb} MB`}
            />
            <MetricTile
              label="Disk"
              icon={HardDrive}
              value={
                m.disk.used_percent != null
                  ? `${Math.round(m.disk.used_percent)}%`
                  : null
              }
              hint={
                m.disk.free_gb != null
                  ? `${m.disk.free_gb} GB free`
                  : m.disk.detail
              }
            />
            <MetricTile
              label="Temperature"
              icon={Thermometer}
              value={
                m.temperature.available && m.temperature.max_c != null
                  ? `${m.temperature.max_c} °C`
                  : null
              }
              hint={
                m.temperature.available
                  ? Object.keys(m.temperature.sensors ?? {}).join(", ")
                  : "No sensor exposed"
              }
            />
            <MetricTile
              label="GPU"
              icon={Gauge}
              value={
                m.gpu.available && m.gpu.utilization_percent != null
                  ? `${Math.round(m.gpu.utilization_percent)}%`
                  : null
              }
              hint={
                m.gpu.available ? m.gpu.name : (m.gpu.detail ?? "Not available")
              }
            />
            <MetricTile
              label="Agent uptime"
              icon={Timer}
              value={formatUptime(d.uptime_seconds)}
              hint={`v${d.agent_version}`}
            />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Panel
              title="Device"
              icon={Server}
              actions={
                d.hardware.is_jetson ? (
                  <Badge variant="secondary">NVIDIA Jetson</Badge>
                ) : undefined
              }
            >
              {canManage && (
                <form
                  className="mb-3 flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const n = (name ?? d.device_name).trim();
                    if (token && n)
                      void act(
                        "rename",
                        () => agent.renameDevice(token, n),
                        "Device renamed.",
                      );
                  }}
                >
                  <Input
                    aria-label="Device name"
                    value={name ?? d.device_name}
                    maxLength={120}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <Button
                    type="submit"
                    variant="outline"
                    disabled={
                      busy !== null ||
                      !(name ?? "").trim() ||
                      name === d.device_name
                    }
                  >
                    Rename
                  </Button>
                </form>
              )}
              <Row label="Device ID">{d.device_id}</Row>
              <Row label="Name">{d.device_name}</Row>
              <Row label="Registered locally">
                {formatDateTime(d.created_at)}
              </Row>
              <Row label="Host">{d.hardware.hostname}</Row>
              <Row label="OS">{`${d.hardware.os} ${d.hardware.os_release}`}</Row>
              <Row label="Architecture">{d.hardware.architecture}</Row>
              <Row label="CPU">{d.hardware.cpu_model}</Row>
              <Row label="Board">{d.hardware.board_model}</Row>
              <Row label="Memory">{`${d.hardware.memory_total_mb} MB`}</Row>
              <Row label="OpenCV">{`${d.hardware.opencv}${d.hardware.opencv_cuda ? " (CUDA)" : " (CPU)"}`}</Row>
              <Row label="Python">{d.hardware.python}</Row>
            </Panel>

            <Panel
              title="Central server"
              icon={Link2}
              actions={
                syncMeta && (
                  <StatusIndicator
                    tone={syncMeta.tone}
                    label={syncMeta.label}
                    pulse={sync?.state === "online"}
                  />
                )
              }
            >
              {sync && (
                <div className="flex flex-col gap-3">
                  {sync.reason && <p className="text-sm">{sync.reason}</p>}
                  {sync.last_error && sync.state !== "online" && (
                    <p className="font-mono text-xs break-all text-muted-foreground">
                      {sync.last_error}
                    </p>
                  )}
                  <div>
                    <Row label="Server">{sync.link?.server_url}</Row>
                    <Row label="Transport">
                      {sync.link
                        ? sync.tls.https
                          ? "HTTPS (certificate verified)"
                          : "HTTP — insecure, lab only"
                        : null}
                    </Row>
                    <Row label="Last heartbeat">
                      {formatDateTime(sync.link?.last_heartbeat_at)}
                    </Row>
                    <Row label="Last upload">
                      {formatDateTime(sync.link?.last_sync_at)}
                    </Row>
                    <Row label="Events waiting">{`${sync.queue.pending}${sync.queue.dropped ? ` (dropped ${sync.queue.dropped})` : ""}`}</Row>
                    <Row label="Uploaded / duplicate / rejected">{`${sync.uploaded} / ${sync.duplicates} / ${sync.rejected}`}</Row>
                    <Row label="Configuration version">
                      {sync.link ? String(sync.link.config_version) : null}
                    </Row>
                  </div>
                  {canManage && linked && (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy !== null}
                        onClick={() =>
                          token && act("sync", () => agent.syncNow(token))
                        }
                      >
                        <RefreshCw className="size-4" /> Sync now
                      </Button>
                      {confirmUnlink ? (
                        <>
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={busy !== null}
                            onClick={() =>
                              token &&
                              act(
                                "unlink",
                                () => agent.unlinkServer(token),
                                "Unlinked. Local data is kept; queued uploads were discarded.",
                              ).then(() => setConfirmUnlink(false))
                            }
                          >
                            Confirm unlink
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmUnlink(false)}
                          >
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busy !== null}
                          onClick={() => setConfirmUnlink(true)}
                        >
                          <Unlink className="size-4" /> Unlink
                        </Button>
                      )}
                    </div>
                  )}
                  {canManage && !linked && (
                    <form
                      className="flex flex-col gap-2 rounded-md border p-3"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (token)
                          void act(
                            "link",
                            () =>
                              agent.linkServer(
                                token,
                                serverUrl.trim(),
                                enrollment.trim(),
                              ),
                            "Registered with the central server.",
                          ).then(() => setEnrollment(""));
                      }}
                    >
                      <Label htmlFor="server-url">
                        Register with a central server
                      </Label>
                      <Input
                        id="server-url"
                        placeholder="https://server.example.com"
                        value={serverUrl}
                        onChange={(e) => setServerUrl(e.target.value)}
                      />
                      <Input
                        type="password"
                        autoComplete="off"
                        aria-label="Enrollment token"
                        placeholder="Enrollment token"
                        value={enrollment}
                        onChange={(e) => setEnrollment(e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">
                        The token is used once; the device then authenticates
                        with its own encrypted credential. HTTPS is required.
                      </p>
                      <Button
                        type="submit"
                        size="sm"
                        disabled={
                          busy !== null ||
                          serverUrl.length < 8 ||
                          enrollment.length < 8
                        }
                      >
                        {busy === "link" ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <ShieldCheck className="size-4" />
                        )}
                        Register
                      </Button>
                    </form>
                  )}
                </div>
              )}
            </Panel>

            <Panel title="Network" icon={Network}>
              {m.network.interfaces.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No connected interface.
                </p>
              )}
              {m.network.interfaces.map((i) => (
                <Row key={i.interface} label={i.interface}>
                  {`${i.ipv4.join(", ")}${i.speed_mbps ? ` · ${i.speed_mbps} Mbit/s` : ""}`}
                </Row>
              ))}
              {m.network.throughput && (
                <Row label="Throughput">{`↓ ${m.network.throughput.rx_kbps} kbit/s · ↑ ${m.network.throughput.tx_kbps} kbit/s`}</Row>
              )}
            </Panel>

            <Panel
              title="Storage & backups"
              icon={Archive}
              actions={
                canManage && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy !== null}
                    onClick={() =>
                      token &&
                      act(
                        "backup",
                        () => agent.createBackup(token),
                        "Backup created and verified.",
                      )
                    }
                  >
                    {busy === "backup" ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Archive className="size-4" />
                    )}
                    Back up now
                  </Button>
                )
              }
            >
              <Row label="Events stored">{String(d.storage.events_total)}</Row>
              <Row label="Event database">{`${d.storage.db_size_mb} MB`}</Row>
              <Row label="Retention">{`${d.storage.retention_days} days`}</Row>
              <Row label="Storage">
                {d.storage.storage_paused ? "PAUSED — disk almost full" : "OK"}
              </Row>
              <Row label="Backups kept">{String(d.backups.count)}</Row>
              <Row label="Latest backup">{d.backups.latest?.id}</Row>
            </Panel>
          </div>
        </>
      )}

      <Panel
        title="System log"
        icon={ScrollText}
        actions={
          <Select value={level} onValueChange={setLevel}>
            <SelectTrigger
              size="sm"
              className="w-32"
              aria-label="Minimum level"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LEVELS.map((l) => (
                <SelectItem key={l} value={l}>
                  {l}+
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        bodyClassName="p-0"
      >
        <div className="max-h-96 overflow-auto">
          {logs.data?.length === 0 && (
            <p className="p-4 text-sm text-muted-foreground">No entries.</p>
          )}
          {logs.data?.map((l) => (
            <div
              key={l.id}
              className="grid grid-cols-[10rem_5rem_8rem_1fr] gap-2 border-b px-4 py-1.5 font-mono text-xs last:border-0"
            >
              <span className="text-muted-foreground">
                {formatDateTime(l.at)}
              </span>
              <span
                className={
                  l.level === "ERROR" || l.level === "CRITICAL"
                    ? "text-destructive"
                    : l.level === "WARNING"
                      ? "text-amber-600 dark:text-amber-400"
                      : ""
                }
              >
                {l.level}
              </span>
              <span className="truncate text-muted-foreground">
                {l.component}
              </span>
              <span className="break-all">{l.message}</span>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
