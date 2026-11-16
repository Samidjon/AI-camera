"use client";

import { Loader2, PenLine, Shapes, Trash2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { agent, agentUrl } from "@/lib/agent/client";
import type { Camera, CrossingLine, MotionAlgorithm, VisionConfig, Zone } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";

import { GeometryCanvas } from "./geometry-canvas";

type Pt = [number, number];

const ALGORITHMS: { value: MotionAlgorithm; label: string; hint: string }[] = [
  { value: "mog2", label: "MOG2 (recommended)", hint: "Gaussian mixture background model; handles gradual light changes." },
  { value: "knn", label: "KNN", hint: "Nearest-neighbour background model; good for dynamic backgrounds." },
  { value: "background", label: "Background subtraction", hint: "Running-average background; cheapest, adapts slowly." },
  { value: "frame_diff", label: "Frame differencing", hint: "Compares consecutive frames; fastest, reacts only to change." },
];

function err(e: unknown) {
  return e instanceof ApiError ? e.message : "Edge agent is unreachable.";
}

export function VisionEditorDialog({
  camera,
  open,
  onOpenChange,
}: {
  camera: Camera;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { token } = usePermissions();
  const [config, setConfig] = useState<VisionConfig | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [lines, setLines] = useState<CrossingLine[]>([]);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [mode, setMode] = useState<"zone" | "line" | null>(null);
  const [draft, setDraft] = useState<Pt[]>([]);
  const [shapeName, setShapeName] = useState("");
  const [zoneRole, setZoneRole] = useState<"monitor" | "exclude">("monitor");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [v, z, l] = await Promise.all([
        agent.vision(token, camera.id),
        agent.zones(token, camera.id),
        agent.lines(token, camera.id),
      ]);
      setConfig(v.config);
      setZones(z);
      setLines(l);
      if (camera.status === "online") {
        const t = await agent.streamToken(token, camera.id);
        setSnapshot(agentUrl(t.snapshot_url));
      }
    } catch (e) {
      setError(err(e));
    }
  }, [token, camera.id, camera.status]);

  useEffect(() => {
    // Data comes from the edge agent (external system); load after the dialog opens.
    if (open) void Promise.resolve().then(load);
  }, [open, load]);

  const startShape = (kind: "zone" | "line") => {
    setMode(kind);
    setDraft([]);
    setShapeName(kind === "zone" ? `Zone ${zones.length + 1}` : `Line ${lines.length + 1}`);
    setError(null);
  };

  const finishShape = async (points: Pt[]) => {
    if (!token || !mode) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "zone") {
        const z = await agent.createZone(token, camera.id, { name: shapeName.trim(), role: zoneRole, polygon: points, is_enabled: true });
        setZones((prev) => [...prev, z]);
      } else {
        const l = await agent.createLine(token, camera.id, { name: shapeName.trim(), a: points[0], b: points[1], is_enabled: true });
        setLines((prev) => [...prev, l]);
      }
      setMode(null);
      setDraft([]);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  };

  // Functional update: rapid clicks must not overwrite each other's points.
  const addPoint = (p: Pt) => setDraft((prev) => (mode === "line" && prev.length >= 2 ? prev : [...prev, p]));

  // A line is complete after its second point.
  useEffect(() => {
    if (mode === "line" && draft.length === 2 && !busy) void Promise.resolve().then(() => finishShape(draft));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- finishShape is recreated each render; draft/mode drive this
  }, [mode, draft]);

  const saveConfig = async () => {
    if (!token || !config) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      setConfig(await agent.saveVisionConfig(token, camera.id, config));
      setSaved(true);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof VisionConfig>(k: K, v: VisionConfig[K]) => {
    setSaved(false);
    setConfig((c) => (c ? { ...c, [k]: v } : c));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Motion detection · {camera.name}</DialogTitle>
          <DialogDescription>
            Standard Vision Engine — runs locally on the edge device without AI or internet. Detected areas are motion
            objects, not people.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
          <div className="space-y-3">
            <GeometryCanvas imageUrl={snapshot} zones={zones} lines={lines} draft={draft} mode={mode} onPoint={addPoint} />
            {mode ? (
              <div className="flex flex-wrap items-end gap-2 rounded-md border bg-muted/30 p-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="shape-name" className="text-xs">
                    {mode === "zone" ? "Zone name" : "Line name"}
                  </Label>
                  <Input id="shape-name" className="h-8 w-44" value={shapeName} onChange={(e) => setShapeName(e.target.value)} maxLength={80} />
                </div>
                {mode === "zone" && (
                  <div className="grid gap-1.5">
                    <Label htmlFor="zone-role" className="text-xs">Role</Label>
                    <Select value={zoneRole} onValueChange={(v) => setZoneRole(v as "monitor" | "exclude")}>
                      <SelectTrigger id="zone-role" size="sm" className="w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="monitor">Monitor (events)</SelectItem>
                        <SelectItem value="exclude">Exclude (ignore motion)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <p className="min-w-[200px] flex-1 text-xs text-muted-foreground">
                  {mode === "zone"
                    ? `Click the image to add corners (${draft.length} so far, at least 3).`
                    : draft.length === 0
                      ? "Click the start point (A) of the line."
                      : "Click the end point (B). The dashed arrow shows the “forward” direction."}
                </p>
                <Button variant="ghost" size="sm" onClick={() => setDraft((d) => d.slice(0, -1))} disabled={!draft.length}>
                  <Undo2 /> Undo
                </Button>
                {mode === "zone" && (
                  <Button size="sm" onClick={() => finishShape(draft)} disabled={draft.length < 3 || busy || !shapeName.trim()}>
                    {busy && <Loader2 className="animate-spin" />} Save zone
                  </Button>
                )}
                <Button variant="outline" size="sm" onClick={() => { setMode(null); setDraft([]); }}>
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => startShape("zone")} disabled={!snapshot}>
                  <Shapes /> Draw zone
                </Button>
                <Button variant="outline" size="sm" onClick={() => startShape("line")} disabled={!snapshot}>
                  <PenLine /> Draw crossing line
                </Button>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <ShapeList
                title="Zones"
                empty="No zones. Monitor zones produce enter/exit events; exclude zones hide motion (trees, screens)."
                items={zones.map((z) => ({
                  id: z.id,
                  name: z.name,
                  detail: z.role === "exclude" ? "Excluded from detection" : `Monitor · ${z.occupancy_estimate ?? 0} obj now (estimate)`,
                }))}
                onDelete={async (id) => {
                  if (!token) return;
                  try {
                    await agent.deleteZone(token, camera.id, id);
                    setZones((z) => z.filter((x) => x.id !== id));
                  } catch (e) {
                    setError(err(e));
                  }
                }}
              />
              <ShapeList
                title="Crossing lines"
                empty="No lines. A line counts motion objects crossing it in each direction."
                items={lines.map((l) => ({
                  id: l.id,
                  name: l.name,
                  detail: `Today: ${l.counts_today?.forward ?? 0} forward · ${l.counts_today?.backward ?? 0} backward`,
                }))}
                onDelete={async (id) => {
                  if (!token) return;
                  try {
                    await agent.deleteLine(token, camera.id, id);
                    setLines((l) => l.filter((x) => x.id !== id));
                  } catch (e) {
                    setError(err(e));
                  }
                }}
              />
            </div>
          </div>

          <div className="space-y-4">
            {config ? (
              <>
                <label className="flex items-center justify-between gap-2 text-sm">
                  Motion detection enabled
                  <Switch aria-label="Motion detection enabled" checked={config.enabled} onCheckedChange={(v) => set("enabled", v)} />
                </label>
                <div className="grid gap-1.5">
                  <Label htmlFor="algo">Algorithm</Label>
                  <Select value={config.algorithm} onValueChange={(v) => set("algorithm", v as MotionAlgorithm)}>
                    <SelectTrigger id="algo" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ALGORITHMS.map((a) => (
                        <SelectItem key={a.value} value={a.value}>
                          {a.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">{ALGORITHMS.find((a) => a.value === config.algorithm)?.hint}</p>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="sens">
                    Sensitivity <span className="font-mono text-muted-foreground">{config.sensitivity}</span>
                  </Label>
                  <input
                    id="sens"
                    type="range"
                    min={1}
                    max={100}
                    value={config.sensitivity}
                    onChange={(e) => set("sensitivity", Number(e.target.value))}
                    className="accent-[var(--primary)]"
                  />
                </div>
                <NumberField id="minarea" label="Minimum object area (% of frame)" value={+(config.min_object_area * 100).toFixed(2)}
                  min={0.01} max={50} step={0.05} onChange={(v) => set("min_object_area", v / 100)} />
                <NumberField id="pfps" label="Analysis FPS (frames beyond are skipped)" value={config.process_fps}
                  min={0.5} max={30} step={0.5} onChange={(v) => set("process_fps", v)} />
                <NumberField id="endd" label="Motion ends after (s without motion)" value={config.motion_end_delay_s}
                  min={0.2} max={60} step={0.5} onChange={(v) => set("motion_end_delay_s", v)} />
                <NumberField id="morph" label="Noise reduction (morphology passes)" value={config.morphology_iterations}
                  min={0} max={6} step={1} onChange={(v) => set("morphology_iterations", v)} />
                <label className="flex items-center justify-between gap-2 text-sm">
                  Detect only inside monitor zones
                  <Switch aria-label="Detect only inside monitor zones" checked={config.restrict_to_zones}
                    onCheckedChange={(v) => set("restrict_to_zones", v)} />
                </label>
                <Button className="w-full" onClick={saveConfig} disabled={busy}>
                  {busy && <Loader2 className="animate-spin" />}
                  {saved ? "Saved" : "Save detection settings"}
                </Button>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Loading settings…</p>
            )}
          </div>
        </div>
        {error && (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </DialogContent>
    </Dialog>
  );
}

function NumberField(props: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={props.id} className="text-xs">
        {props.label}
      </Label>
      <Input
        id={props.id}
        type="number"
        className="h-8"
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) props.onChange(Math.min(props.max, Math.max(props.min, v)));
        }}
      />
    </div>
  );
}

function ShapeList({
  title,
  empty,
  items,
  onDelete,
}: {
  title: string;
  empty: string;
  items: { id: string; name: string; detail: string }[];
  onDelete: (id: string) => void;
}) {
  return (
    <div className="rounded-md border">
      <div className="border-b px-3 py-2 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{title}</div>
      {items.length === 0 ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="divide-y">
          {items.map((it) => (
            <li key={it.id} className="flex items-center gap-2 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{it.name}</div>
                <div className="truncate text-xs text-muted-foreground">{it.detail}</div>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label={`Delete ${it.name}`} onClick={() => onDelete(it.id)}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
