"use client";

import { MapPin } from "lucide-react";

import type { Camera } from "@/lib/agent/types";

import { CameraPreview } from "./camera-preview";
import { CameraStatusBadge, SOURCE_LABEL } from "./camera-status";
import { StreamStats } from "./stream-stats";

export function CameraTile({
  camera,
  live,
  onRequestLive,
  onOpen,
}: {
  camera: Camera;
  live: boolean;
  onRequestLive: () => void;
  onOpen: () => void;
}) {
  return (
    <article className="group overflow-hidden rounded-lg border bg-card transition-colors hover:border-foreground/20">
      <div className="relative">
        <CameraPreview camera={camera} live={live} onRequestLive={onRequestLive} />
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent px-2.5 py-2">
          <span className="rounded bg-black/50 px-1.5 py-0.5 font-mono text-[10px] text-white/80 uppercase">
            {SOURCE_LABEL[camera.source_type]}
          </span>
          {camera.status === "online" && (
            <span className="rounded bg-black/50 px-1.5 py-0.5 font-mono text-[10px] text-white/90">
              <StreamStats camera={camera} />
            </span>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
        aria-label={`Open details for ${camera.name}`}
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{camera.name}</div>
          <div className="flex items-center gap-1 truncate text-xs text-muted-foreground">
            {camera.location ? (
              <>
                <MapPin className="size-3 shrink-0" aria-hidden />
                {camera.location}
              </>
            ) : (
              <span className="truncate font-mono text-[11px]">{camera.source}</span>
            )}
          </div>
        </div>
        <CameraStatusBadge status={camera.status} />
      </button>
    </article>
  );
}
