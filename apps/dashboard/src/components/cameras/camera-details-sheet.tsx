"use client";

import { Loader2, Pencil, PlugZap, Power, PowerOff, ScanSearch, Trash2 } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { agent } from "@/lib/agent/client";
import type { Camera, CameraGroup, TestResult } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";

import { OverlayToggles } from "@/components/vision/overlay-toggles";
import { VisionEditorDialog } from "@/components/vision/vision-editor-dialog";
import { VisionStatus } from "@/components/vision/vision-status";

import { CameraPreview } from "./camera-preview";
import { CameraStatusBadge, FAILURE_HINT, SOURCE_LABEL } from "./camera-status";
import { StreamStats } from "./stream-stats";

export function CameraDetailsSheet({
  camera,
  groups,
  open,
  onOpenChange,
  onChanged,
  onEdit,
  onDelete,
}: {
  camera: Camera | null;
  groups: CameraGroup[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { can, token } = usePermissions();
  const [busy, setBusy] = useState<"connect" | "disconnect" | "test" | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState<string[]>(["boxes", "tracks", "zones", "lines"]);
  const [editorOpen, setEditorOpen] = useState(false);

  if (!camera) return null;
  const group = groups.find((g) => g.id === camera.group_id);
  const running = !["offline", "disabled"].includes(camera.status);

  async function run(kind: "connect" | "disconnect" | "test") {
    if (!token || !camera) return;
    setBusy(kind);
    setError(null);
    if (kind === "test") setTest(null);
    try {
      if (kind === "connect") await agent.connect(token, camera.id);
      if (kind === "disconnect") await agent.disconnect(token, camera.id);
      if (kind === "test") setTest(await agent.testCamera(token, camera.id));
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Edge agent is unreachable.");
    } finally {
      setBusy(null);
    }
  }

  const rows: [string, React.ReactNode][] = [
    ["Status", <CameraStatusBadge key="s" status={camera.status} />],
    ["Stream", <StreamStats key="r" camera={camera} />],
    ["Advertised FPS", camera.nominal_fps != null ? camera.nominal_fps.toFixed(1) : "—"],
    ["Source", <span key="src" className="font-mono text-xs break-all">{camera.source}</span>],
    ["Type", SOURCE_LABEL[camera.source_type]],
    ["Credentials", camera.has_credentials ? "Stored (encrypted)" : "None"],
    ["Location", camera.location ?? "—"],
    ["Group", group?.name ?? "—"],
    ["Last connected", formatDateTime(camera.last_connected_at)],
    ["Last frame", formatDateTime(camera.last_frame_at)],
    ["Frames this session", camera.frames_received.toLocaleString()],
    ["Reconnect attempts", camera.reconnect_attempts],
    ["Camera ID", <span key="id" className="font-mono text-[11px]">{camera.id}</span>],
    ["Created", formatDateTime(camera.created_at)],
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle>{camera.name}</SheetTitle>
          <SheetDescription>{camera.location ?? "No location set"}</SheetDescription>
        </SheetHeader>

        <CameraPreview camera={camera} live={open} layers={layers} />
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          <span className="text-[11px] tracking-wide text-muted-foreground uppercase">Overlays</span>
          <OverlayToggles value={layers} onChange={setLayers} />
        </div>

        <div className="flex flex-wrap gap-2 border-b p-4">
          {can("cameras:control") &&
            (running ? (
              <Button variant="outline" size="sm" onClick={() => run("disconnect")} disabled={busy !== null}>
                {busy === "disconnect" ? <Loader2 className="animate-spin" /> : <PowerOff />}
                Disconnect
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => run("connect")}
                disabled={busy !== null || !camera.is_enabled}
                title={camera.is_enabled ? undefined : "Enable the camera first"}
              >
                {busy === "connect" ? <Loader2 className="animate-spin" /> : <Power />}
                Connect
              </Button>
            ))}
          {can("cameras:control") && (
            <Button variant="outline" size="sm" onClick={() => run("test")} disabled={busy !== null}>
              {busy === "test" ? <Loader2 className="animate-spin" /> : <PlugZap />}
              Test connection
            </Button>
          )}
          {can("cameras:manage") && (
            <>
              <Button variant="outline" size="sm" onClick={() => setEditorOpen(true)}>
                <ScanSearch /> Detection & zones
              </Button>
              <Button variant="outline" size="sm" onClick={onEdit}>
                <Pencil /> Edit
              </Button>
              <Button variant="destructive" size="sm" className="ml-auto" onClick={onDelete}>
                <Trash2 /> Delete
              </Button>
            </>
          )}
        </div>

        <div className="space-y-3 p-4">
          <label className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
            <span>
              AI analysis for this camera
              <span className="block text-xs text-muted-foreground">
                Person detection (when the AI Engine is on). Motion detection always runs.
              </span>
            </span>
            <Switch
              aria-label="AI analysis for this camera"
              checked={camera.options.ai_enabled !== false}
              disabled={!can("cameras:manage") || busy !== null}
              onCheckedChange={async (v) => {
                if (!token) return;
                setError(null);
                try {
                  await agent.updateCamera(token, camera.id, { options: { ai_enabled: v } });
                  onChanged();
                } catch (err) {
                  setError(err instanceof ApiError ? err.message : "Edge agent is unreachable.");
                }
              }}
            />
          </label>
          <VisionStatus cameraId={camera.id} />
          {camera.last_error && camera.status !== "online" && (
            <Alert variant={camera.status === "error" ? "destructive" : "default"}>
              <AlertTitle>{camera.last_error}</AlertTitle>
              {camera.failure_kind && <AlertDescription>{FAILURE_HINT[camera.failure_kind]}</AlertDescription>}
            </Alert>
          )}
          {test && (
            <Alert variant={test.ok ? "default" : "destructive"} role="status">
              <AlertTitle>{test.ok ? "Connection OK" : "Connection failed"}</AlertTitle>
              <AlertDescription>
                {test.message}
                {test.ok && test.resolution && ` · ${test.resolution.width}×${test.resolution.height}`}
              </AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-4 gap-y-2 text-sm">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </SheetContent>
      {editorOpen && <VisionEditorDialog camera={camera} open={editorOpen} onOpenChange={setEditorOpen} />}
    </Sheet>
  );
}
