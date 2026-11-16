"use client";

import { KeyRound, Loader2, ShieldOff, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ApiError } from "@/lib/api/client";
import { fleet, type FleetDeviceDetail, type SyncLog } from "@/lib/api/fleet";
import { formatDateTime } from "@/lib/format";

import { ConfigPanel } from "./config-panel";
import { FleetStatusIndicator, timeAgo } from "./fleet-status";

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

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2 border-b px-4 py-4">
      <h3 className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function DeviceSheet({
  token,
  deviceId,
  canManage,
  revision,
  onOpenChange,
  onChanged,
}: {
  token: string;
  deviceId: string | null;
  canManage: boolean;
  revision: number;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const [device, setDevice] = useState<FleetDeviceDetail | null>(null);
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"revoke" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    if (!deviceId) return;
    const [d, l] = await Promise.all([
      fleet.device(token, deviceId),
      fleet.syncLogs(token, deviceId),
    ]);
    setDevice(d);
    setLogs(l);
    setNow(Date.now());
  }, [token, deviceId]);

  useEffect(() => {
    void Promise.resolve()
      .then(load)
      .catch((e) =>
        setError(
          e instanceof ApiError ? e.message : "Central server is unreachable.",
        ),
      );
  }, [load, revision]);

  async function act(key: string, fn: () => Promise<unknown>, close = false) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
      if (close) onOpenChange(false);
      else await load();
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Central server is unreachable.",
      );
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  }

  const hw = device?.metrics.hardware ?? {};
  return (
    <Sheet open={deviceId !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-3">
            {device?.name ?? "Device"}{" "}
            {device && <FleetStatusIndicator status={device.status} />}
          </SheetTitle>
          <SheetDescription>
            {device?.status_reason ?? (device ? "Healthy" : "Loading…")}
          </SheetDescription>
        </SheetHeader>
        {error && (
          <Alert variant="destructive" className="m-4 w-auto">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {device && (
          <>
            <Section title="Health">
              <Row label="Last heartbeat">{`${timeAgo(device.last_heartbeat_at, now)} (every ${device.heartbeat_interval_s}s)`}</Row>
              <Row label="Agent version">{device.agent_version}</Row>
              <Row label="CPU / memory">
                {hw.cpu?.percent != null
                  ? `${Math.round(hw.cpu.percent)}% / ${Math.round(hw.memory?.percent ?? 0)}%`
                  : null}
              </Row>
              <Row label="Disk">
                {hw.disk?.used_percent != null
                  ? `${Math.round(hw.disk.used_percent)}% used, ${hw.disk.free_gb} GB free`
                  : null}
              </Row>
              <Row label="Temperature">
                {hw.temperature?.available
                  ? `${hw.temperature.max_c} °C`
                  : "not exposed"}
              </Row>
              <Row label="GPU">
                {hw.gpu?.available
                  ? `${hw.gpu.name ?? "GPU"} · ${hw.gpu.utilization_percent ?? "?"}%`
                  : "none"}
              </Row>
              <Row label="Network">
                {hw.network
                  ? `${hw.network.connected ? "connected" : "down"} · ${(
                      hw.network.interfaces ?? []
                    )
                      .map((n) => `${n.interface} ${n.ipv4.join(", ")}`)
                      .join("; ")}`
                  : null}
              </Row>
              <Row label="AI">
                {device.ai_status.enabled
                  ? `on · ${device.ai_status.mode}`
                  : "off"}
              </Row>
              <Row label="Events waiting on device">
                {String(device.sync_status.pending ?? 0)}
              </Row>
              <Row label="Address">{device.last_seen_ip}</Row>
              <Row label="Registered">
                {formatDateTime(device.registered_at)}
              </Row>
              <Row label="Device ID">{device.id}</Row>
            </Section>

            <Section title={`Cameras (${device.cameras.length})`}>
              {device.cameras.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  The device reports no cameras.
                </p>
              )}
              {device.cameras.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center gap-2 border-b py-1.5 text-sm last:border-0"
                >
                  <span className="flex-1 truncate">{c.name}</span>
                  <Badge variant="outline">{c.source_type}</Badge>
                  {c.ai_enabled === false && (
                    <Badge variant="secondary">AI off</Badge>
                  )}
                  <Badge
                    variant={
                      c.status === "online"
                        ? "default"
                        : c.status === "error"
                          ? "destructive"
                          : "secondary"
                    }
                  >
                    {c.status}
                  </Badge>
                  <span className="w-14 text-right font-mono text-xs text-muted-foreground">
                    {c.fps ? `${c.fps.toFixed(1)} fps` : ""}
                  </span>
                </div>
              ))}
            </Section>

            <Section title="Configuration">
              <ConfigPanel
                token={token}
                deviceId={device.id}
                cameras={device.cameras}
                canManage={canManage && !device.revoked_at}
                revision={revision}
              />
            </Section>

            <Section title="Synchronization">
              {logs.length === 0 && (
                <p className="text-sm text-muted-foreground">No uploads yet.</p>
              )}
              {logs.slice(0, 10).map((l) => (
                <div key={l.id} className="flex items-center gap-2 text-xs">
                  <span className="w-28 text-muted-foreground">
                    {formatDateTime(l.created_at)}
                  </span>
                  <span className="w-24">{l.kind}</span>
                  <Badge
                    variant={
                      l.status === "ok"
                        ? "default"
                        : l.status === "failed"
                          ? "destructive"
                          : "secondary"
                    }
                  >
                    {l.status}
                  </Badge>
                  {l.kind === "events" && (
                    <span className="font-mono text-muted-foreground">
                      +{l.accepted} · dup {l.duplicate} · rej {l.rejected}
                    </span>
                  )}
                </div>
              ))}
            </Section>

            {canManage && !device.revoked_at && (
              <Section title="Security">
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null || device.rotate_credentials}
                    onClick={() =>
                      void act("rotate", () => fleet.rotate(token, device.id))
                    }
                  >
                    {busy === "rotate" ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <KeyRound className="size-4" />
                    )}
                    {device.rotate_credentials
                      ? "Rotation requested"
                      : "Rotate credential"}
                  </Button>
                  {confirm === "revoke" ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy !== null}
                      onClick={() =>
                        void act("revoke", () => fleet.revoke(token, device.id))
                      }
                    >
                      Confirm revoke
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setConfirm("revoke")}
                    >
                      <ShieldOff className="size-4" /> Revoke
                    </Button>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  A revoked device stops uploading and keeps working locally. It
                  can be enrolled again with a new token.
                </p>
              </Section>
            )}
            {canManage && (
              <div className="px-4 py-4">
                {confirm === "delete" ? (
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy !== null}
                      onClick={() =>
                        void act(
                          "delete",
                          () => fleet.remove(token, device.id),
                          true,
                        )
                      }
                    >
                      Delete permanently
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirm(null)}
                    >
                      Cancel
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      Its events stay on the server.
                    </span>
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => setConfirm("delete")}
                  >
                    <Trash2 className="size-4" /> Delete device
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
