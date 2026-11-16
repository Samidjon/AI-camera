"use client";

import { Check, Loader2, Plus, Radar, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { agent } from "@/lib/agent/client";
import type { DiscoveredDevice, LocalNetwork, ScanJob } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";

import type { CameraPrefill } from "./camera-form-dialog";

function deviceName(d: DiscoveredDevice): string {
  const n = d.onvif?.name || d.onvif?.hardware;
  if (n) return n;
  if (d.vendor) return `${d.vendor[0].toUpperCase()}${d.vendor.slice(1)} camera`;
  return d.rtsp?.server ? `RTSP · ${d.rtsp.server}` : "RTSP device";
}

/**
 * Finds cameras on the edge device's local network (ONVIF WS-Discovery + RTSP service scan).
 * Discovery only: adding a camera still requires its own username / password.
 */
export function NetworkScanDialog({
  open,
  onOpenChange,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (prefill: CameraPrefill) => void;
}) {
  const { token } = usePermissions();
  const [networks, setNetworks] = useState<LocalNetwork[]>([]);
  const [custom, setCustom] = useState("");
  const [job, setJob] = useState<ScanJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !token) return;
    void Promise.resolve().then(() =>
      agent
        .localNetworks(token)
        .then(setNetworks)
        .catch((e) => setError(e instanceof ApiError ? e.message : "Edge agent is unreachable.")),
    );
  }, [open, token]);

  // Poll the running scan.
  useEffect(() => {
    if (!token || !job || job.status !== "running") return;
    const id = setTimeout(() => {
      agent
        .scan(token, job.id)
        .then(setJob)
        .catch((e) => setError(e instanceof ApiError ? e.message : "Edge agent is unreachable."));
    }, 700);
    return () => clearTimeout(id);
  }, [token, job]);

  async function start() {
    if (!token) return;
    setError(null);
    try {
      setJob(await agent.startScan(token, custom.trim() || undefined));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Edge agent is unreachable.");
    }
  }

  const running = job?.status === "running";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Scan network for cameras</DialogTitle>
          <DialogDescription>
            Looks for ONVIF cameras (WS-Discovery) and RTSP video servers on the edge device&apos;s local network.
            Only private networks are scanned. Viewing a camera still requires its own username and password.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-end gap-3">
          <div className="grid min-w-[220px] flex-1 gap-1.5">
            <Label htmlFor="scan-net" className="text-xs">
              Network (optional)
            </Label>
            <Input
              id="scan-net"
              placeholder={networks.map((n) => n.network).join(", ") || "192.168.1.0/24"}
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              disabled={running}
              className="font-mono text-xs"
            />
          </div>
          {running ? (
            <Button variant="outline" onClick={() => token && job && agent.cancelScan(token, job.id).then(setJob)}>
              <Square /> Stop
            </Button>
          ) : (
            <Button onClick={start}>
              <Radar /> {job ? "Scan again" : "Start scan"}
            </Button>
          )}
        </div>
        {networks.length > 0 && !custom && (
          <p className="text-xs text-muted-foreground">
            Default: this device&apos;s networks —{" "}
            {networks.map((n) => `${n.network} (${n.hosts} addresses)`).join(", ")}
          </p>
        )}

        {job && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {running ? "Scanning" : job.status === "done" ? "Finished" : job.status} · {job.networks.join(", ")}
              </span>
              <span className="font-mono">{Math.round(job.progress * 100)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(job.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full bg-primary transition-all" style={{ width: `${job.progress * 100}%` }} />
            </div>
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {job?.error && (
          <Alert variant="destructive">
            <AlertDescription>{job.error}</AlertDescription>
          </Alert>
        )}

        {job && !running && job.devices.length === 0 && job.status === "done" && (
          <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            No cameras found. Check that cameras are powered and on the same network, or enter their network above.
          </p>
        )}

        {job && job.devices.length > 0 && (
          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Address</TableHead>
                  <TableHead>Device</TableHead>
                  <TableHead>Found via</TableHead>
                  <TableHead>Suggested source</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {job.devices.map((d) => (
                  <TableRow key={d.ip}>
                    <TableCell className="font-mono text-xs">{d.ip}</TableCell>
                    <TableCell className="text-sm">
                      {deviceName(d)}
                      {d.path_hint && <div className="text-[11px] text-muted-foreground">{d.path_hint}</div>}
                    </TableCell>
                    <TableCell className="space-x-1">
                      {d.onvif && <Badge variant="secondary" className="text-[10px]">ONVIF</Badge>}
                      {d.web && (
                        <Badge variant="outline" className="text-[10px]">
                          Web UI :{d.web.port}
                        </Badge>
                      )}
                      {d.rtsp && (
                        <Badge variant="outline" className="font-mono text-[10px]">
                          RTSP :{d.rtsp.port}
                          {d.rtsp.status === 401 ? " · auth" : ""}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="max-w-[220px] truncate font-mono text-[11px]">{d.suggested_source_uri}</TableCell>
                    <TableCell className="text-right">
                      {d.already_added ? (
                        <span className="inline-flex items-center gap-1 text-xs text-status-online">
                          <Check className="size-3.5" /> Added
                        </span>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            onAdd({ name: deviceName(d), source_type: d.suggested_source_type, source_uri: d.suggested_source_uri })
                          }
                        >
                          <Plus /> Add
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {running && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Probing addresses — this takes about 10–30 seconds.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
