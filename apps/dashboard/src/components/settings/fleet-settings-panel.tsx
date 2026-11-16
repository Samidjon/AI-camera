"use client";

import { Loader2, Send, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { ApiError } from "@/lib/api/client";
import { fleet, type DeviceConfigDoc } from "@/lib/api/fleet";

const KEEP = "__keep__";

/**
 * Camera, device, AI and event settings for every edge device at once. Each device gets a new,
 * versioned configuration through the normal API (base_version, acknowledgement, rollback).
 */
export function FleetSettingsPanel({ token }: { token: string }) {
  const [ai, setAi] = useState(KEEP);
  const [retention, setRetention] = useState("");
  const [heartbeat, setHeartbeat] = useState("");
  const [sensitivity, setSensitivity] = useState("");
  const [cameraAi, setCameraAi] = useState(KEEP);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<
    { name: string; ok: boolean; text: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);

  const nothing =
    ai === KEEP &&
    !retention &&
    !heartbeat &&
    !sensitivity &&
    cameraAi === KEEP;

  async function apply() {
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      const devices = (await fleet.devices(token)).filter((d) => !d.revoked_at);
      const out: { name: string; ok: boolean; text: string }[] = [];
      for (const d of devices) {
        try {
          const patch: DeviceConfigDoc = {};
          if (ai !== KEEP) patch.ai_enabled = ai === "on";
          if (retention) patch.events = { retention_days: Number(retention) };
          if (heartbeat) patch.heartbeat_interval_s = Number(heartbeat);
          if (sensitivity || cameraAi !== KEEP) {
            const detail = await fleet.device(token, d.id);
            patch.cameras = Object.fromEntries(
              detail.cameras
                .filter((c) => c.external_id)
                .map((c) => [
                  c.external_id as string,
                  {
                    ...(sensitivity
                      ? { motion: { sensitivity: Number(sensitivity) } }
                      : {}),
                    ...(cameraAi !== KEEP
                      ? { ai_enabled: cameraAi === "on" }
                      : {}),
                  },
                ]),
            );
          }
          const state = await fleet.config(token, d.id);
          const r = await fleet.patchConfig(token, d.id, {
            base_version: state.version,
            config: patch,
            comment: "fleet settings",
          });
          out.push({
            name: d.name,
            ok: true,
            text:
              r.version === state.version
                ? "already up to date"
                : `v${r.version} queued`,
          });
        } catch (e) {
          out.push({
            name: d.name,
            ok: false,
            text: e instanceof ApiError ? e.message : "failed",
          });
        }
        setResults([...out]);
      }
      if (devices.length === 0) setError("No registered devices.");
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Central server is unreachable.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Fleet settings — cameras, devices, AI, events"
      icon={SlidersHorizontal}
    >
      <p className="mb-3 text-sm text-muted-foreground">
        Applies to every registered device as a new configuration version.
        Devices apply it at their next heartbeat and acknowledge it; each
        version can be rolled back on the device page. Leave a field empty to
        keep it unchanged.
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label>AI engine (devices)</Label>
          <Select value={ai} onValueChange={setAi}>
            <SelectTrigger aria-label="AI engine on all devices">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={KEEP}>Keep</SelectItem>
              <SelectItem value="on">Enable on all devices</SelectItem>
              <SelectItem value="off">Disable on all devices</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>AI per camera</Label>
          <Select value={cameraAi} onValueChange={setCameraAi}>
            <SelectTrigger aria-label="AI on all cameras">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={KEEP}>Keep</SelectItem>
              <SelectItem value="on">On for every camera</SelectItem>
              <SelectItem value="off">Off for every camera</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="fs-sens">Motion sensitivity (all cameras)</Label>
          <Input
            id="fs-sens"
            type="number"
            min={1}
            max={100}
            placeholder="keep"
            value={sensitivity}
            onChange={(e) => setSensitivity(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="fs-ret">
            Local event retention on devices (days)
          </Label>
          <Input
            id="fs-ret"
            type="number"
            min={1}
            max={3650}
            placeholder="keep"
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="fs-hb">Heartbeat interval (s)</Label>
          <Input
            id="fs-hb"
            type="number"
            min={5}
            max={3600}
            placeholder="keep"
            value={heartbeat}
            onChange={(e) => setHeartbeat(e.target.value)}
          />
        </div>
        <div className="flex items-end">
          <Button onClick={apply} disabled={busy || nothing}>
            {busy ? <Loader2 className="animate-spin" /> : <Send />} Apply to
            all devices
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {results.length > 0 && (
        <ul
          className="mt-3 divide-y rounded-md border text-sm"
          data-testid="fleet-results"
        >
          {results.map((r) => (
            <li key={r.name} className="flex justify-between gap-3 px-3 py-1.5">
              <span>{r.name}</span>
              <span
                className={r.ok ? "text-status-online" : "text-destructive"}
              >
                {r.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
