"use client";

import {
  Check,
  ChevronDown,
  Expand,
  Grid2x2,
  Maximize,
  Minimize,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import {
  StatusIndicator,
  type StatusTone,
} from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useCameras } from "@/hooks/use-cameras";
import {
  useLiveStream,
  type LiveCameraStatus,
} from "@/lib/agent/use-live-stream";
import type { Camera } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";
import { cn } from "@/lib/utils";

type GridSize = 1 | 4 | 9 | 16;
const GRID: Record<GridSize, { cols: string; width: number; fps: number }> = {
  1: { cols: "grid-cols-1", width: 1280, fps: 12 },
  4: { cols: "grid-cols-1 sm:grid-cols-2", width: 800, fps: 10 },
  9: { cols: "grid-cols-2 lg:grid-cols-3", width: 560, fps: 8 },
  16: { cols: "grid-cols-2 md:grid-cols-3 xl:grid-cols-4", width: 420, fps: 6 },
};

/** Overlay groups mapped onto the layers the agent draws into the frames. */
const OVERLAYS = [
  { id: "motion", label: "Motion", layers: ["regions"] },
  { id: "detection", label: "Detection", layers: ["boxes", "people"] },
  { id: "tracking", label: "Tracking", layers: ["tracks"] },
  { id: "rules", label: "Zones & lines", layers: ["zones", "lines"] },
] as const;

const STREAM_TONE: Record<string, StatusTone> = {
  online: "online",
  connecting: "warning",
  reconnecting: "warning",
  error: "error",
  offline: "idle",
  disabled: "idle",
  not_found: "error",
};

function Tile({
  camera,
  status,
  displayedFps,
  bind,
  onOpen,
}: {
  camera: Camera;
  status: LiveCameraStatus | undefined;
  displayedFps: number;
  bind: (el: HTMLCanvasElement | null) => void;
  onOpen: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const st = status?.status ?? camera.status;
  return (
    <div
      ref={box}
      data-testid="live-tile"
      data-camera={camera.name}
      className="group relative aspect-video overflow-hidden rounded-md border bg-black"
    >
      <canvas
        ref={bind}
        className="size-full object-contain"
        onDoubleClick={onOpen}
        aria-label={`Live video ${camera.name}`}
      />
      {st !== "online" && (
        <div className="absolute inset-0 grid place-items-center p-4 text-center text-xs text-white/70">
          {st === "connecting" || st === "reconnecting"
            ? "Connecting to camera…"
            : (status?.error ?? `Camera ${st}`)}
        </div>
      )}
      <div className="absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent px-2 py-1.5 text-xs text-white">
        <StatusIndicator
          tone={STREAM_TONE[st] ?? "idle"}
          label={st}
          className="text-white"
        />
        <span className="truncate font-medium">{camera.name}</span>
        <span
          className="ml-auto font-mono text-[10px] text-white/80"
          data-testid="tile-fps"
          title="Displayed / camera FPS"
        >
          {displayedFps} / {status?.fps != null ? status.fps.toFixed(1) : "—"}{" "}
          fps
        </span>
      </div>
      <div className="absolute right-1.5 bottom-1.5 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <Button
          size="icon-sm"
          variant="secondary"
          aria-label={`Single view ${camera.name}`}
          onClick={onOpen}
        >
          <Expand />
        </Button>
        <Button
          size="icon-sm"
          variant="secondary"
          aria-label={`Fullscreen ${camera.name}`}
          onClick={() => void box.current?.requestFullscreen?.()}
        >
          <Maximize />
        </Button>
      </div>
    </div>
  );
}

export function MonitoringView() {
  const { token } = usePermissions();
  const { cameras, loaded, error } = useCameras();
  const [grid, setGrid] = useState<GridSize>(4);
  const [selection, setSelection] = useState<string[] | null>(null); // null = first N enabled cameras
  const [overlays, setOverlays] = useState<string[]>(["motion", "detection"]);
  const [fullscreen, setFullscreen] = useState(false);
  const wall = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onChange = () =>
      setFullscreen(document.fullscreenElement === wall.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const enabled = useMemo(() => cameras.filter((c) => c.is_enabled), [cameras]);
  const chosen = useMemo(() => {
    const ids = selection ?? enabled.map((c) => c.id);
    return ids
      .map((id) => cameras.find((c) => c.id === id))
      .filter((c): c is Camera => Boolean(c))
      .slice(0, grid);
  }, [selection, enabled, cameras, grid]);

  const layers = useMemo(
    () =>
      OVERLAYS.filter((o) => overlays.includes(o.id)).flatMap((o) => [
        ...o.layers,
      ]),
    [overlays],
  );
  const cfg = GRID[grid];
  const stream = useLiveStream(
    token,
    chosen.map((c) => c.id),
    { layers, maxFps: cfg.fps, maxWidth: cfg.width },
  );

  const openSingle = useCallback((id: string) => {
    setSelection((sel) => [id, ...(sel ?? []).filter((x) => x !== id)]);
    setGrid(1);
  }, []);

  const toggleCamera = (id: string) =>
    setSelection((sel) => {
      const cur = sel ?? enabled.map((c) => c.id);
      return cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    });

  const streamTone: StatusTone =
    stream.state === "live"
      ? "online"
      : stream.state === "connecting"
        ? "warning"
        : "error";
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Live monitoring"
        description="All cameras of this edge agent over one multiplexed stream. Overlays are drawn by the agent into the frames."
        actions={
          <StatusIndicator
            tone={streamTone}
            label={
              stream.state === "live"
                ? "Stream live"
                : stream.state === "unauthorized"
                  ? "Not authorized"
                  : stream.state === "connecting"
                    ? "Connecting…"
                    : "Reconnecting…"
            }
            pulse={stream.state === "live"}
          />
        }
      />

      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-2">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={String(grid)}
          onValueChange={(v) => v && setGrid(Number(v) as GridSize)}
          aria-label="Grid size"
        >
          <ToggleGroupItem value="1" aria-label="1 camera">
            <Square /> 1
          </ToggleGroupItem>
          <ToggleGroupItem value="4" aria-label="4 cameras">
            <Grid2x2 /> 4
          </ToggleGroupItem>
          <ToggleGroupItem value="9" aria-label="9 cameras">
            9
          </ToggleGroupItem>
          <ToggleGroupItem value="16" aria-label="16 cameras">
            16
          </ToggleGroupItem>
        </ToggleGroup>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              Cameras ({chosen.length}/{cameras.length}) <ChevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel>Show on the wall</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {cameras.map((c) => (
              <DropdownMenuCheckboxItem
                key={c.id}
                checked={chosen.some((x) => x.id === c.id)}
                onSelect={(e) => e.preventDefault()}
                onCheckedChange={() => toggleCamera(c.id)}
              >
                {c.name}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Overlays"
        >
          {OVERLAYS.map((o) => {
            const on = overlays.includes(o.id);
            return (
              <button
                key={o.id}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  setOverlays(
                    on
                      ? overlays.filter((x) => x !== o.id)
                      : [...overlays, o.id],
                  )
                }
                className={cn(
                  "flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on
                    ? "border-primary/50 bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {on && <Check className="size-3" />} {o.label}
              </button>
            );
          })}
        </div>

        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() =>
            fullscreen
              ? document.exitFullscreen()
              : wall.current?.requestFullscreen()
          }
        >
          {fullscreen ? <Minimize /> : <Maximize />}{" "}
          {fullscreen ? "Exit fullscreen" : "Fullscreen"}
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Edge agent unreachable</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}
      {loaded && cameras.length === 0 && (
        <p className="rounded-lg border p-6 text-sm text-muted-foreground">
          No cameras on this edge agent yet — add one on the Cameras page.
        </p>
      )}

      <div
        ref={wall}
        data-testid="live-wall"
        data-grid={grid}
        className={cn(
          "grid gap-2",
          GRID[grid].cols,
          fullscreen && "content-center bg-black p-2",
        )}
      >
        {chosen.map((c) => (
          <Tile
            key={c.id}
            camera={c}
            status={stream.statuses[c.id]}
            displayedFps={stream.received[c.id] ?? 0}
            bind={stream.bindCanvas(c.id)}
            onOpen={() => openSingle(c.id)}
          />
        ))}
      </div>
      {grid > 1 && chosen.length > 0 && chosen.length < grid && (
        <p className="text-xs text-muted-foreground">
          {chosen.length} of {grid} slots used — select more cameras above.
        </p>
      )}
    </div>
  );
}
