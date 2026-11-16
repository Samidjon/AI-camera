"use client";

import { CameraOff, Loader2, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { agent, agentUrl } from "@/lib/agent/client";
import type { Camera } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";
import { cn } from "@/lib/utils";

import { STATUS_META } from "./camera-status";

const MAX_RETRIES = 3;

/**
 * Live preview over MJPEG (multipart/x-mixed-replace) from the edge agent: one long-lived HTTP
 * response per viewer, frames pushed by the agent — no per-frame REST polling. The stream URL
 * carries a short-lived, camera-scoped token (never the user's access token).
 */
export function CameraPreview({
  camera,
  live = true,
  onRequestLive,
  layers = [],
  className,
}: {
  camera: Camera;
  live?: boolean;
  onRequestLive?: () => void;
  /** Agent-drawn overlay layers: regions, boxes, tracks, zones, lines. */
  layers?: string[];
  className?: string;
}) {
  const layerParam = [...layers].sort().join(",");
  const { token } = usePermissions();
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const streaming = camera.status === "online" && live;

  useEffect(() => {
    if (!streaming || !token) return;
    let cancelled = false;
    agent
      .streamToken(token, camera.id)
      .then((t) => {
        if (!cancelled) {
          setFailed(false);
          setSrc(agentUrl(t.stream_url) + (layerParam ? `&layers=${layerParam}` : ""));
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      setSrc(null); // closing the <img> request releases the agent's stream
    };
  }, [streaming, token, camera.id, attempt, layerParam]);

  const meta = STATUS_META[camera.status];

  return (
    <div className={cn("relative aspect-video overflow-hidden bg-black/60", className)}>
      {streaming && !failed && (
        <div className="absolute inset-0 grid place-items-center text-muted-foreground" aria-hidden>
          <Loader2 className="size-5 animate-spin" />
        </div>
      )}
      {streaming && src && !failed && (
        // eslint-disable-next-line @next/next/no-img-element -- MJPEG streams can't go through next/image
        <img
          src={src}
          alt={`Live view: ${camera.name}`}
          className="absolute inset-0 size-full object-contain"
          onError={() => {
            setSrc(null);
            if (attempt < MAX_RETRIES) setTimeout(() => setAttempt((n) => n + 1), 1500);
            else setFailed(true);
          }}
        />
      )}
      {!streaming && (
        <div className="absolute inset-0 grid place-items-center p-3 text-center">
          {camera.status === "online" && !live ? (
            <button
              type="button"
              onClick={onRequestLive}
              className="flex flex-col items-center gap-1.5 rounded-md px-3 py-2 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Play className="size-5" aria-hidden />
              Show live view
            </button>
          ) : (
            <div className="flex flex-col items-center gap-1.5 text-xs text-muted-foreground">
              {camera.status === "connecting" || camera.status === "reconnecting" ? (
                <Loader2 className="size-5 animate-spin" aria-hidden />
              ) : (
                <CameraOff className="size-5" aria-hidden />
              )}
              <span>{meta.label}</span>
            </div>
          )}
        </div>
      )}
      {failed && streaming && (
        <div className="absolute inset-0 grid place-items-center text-xs text-muted-foreground">
          <button type="button" className="underline-offset-4 hover:underline" onClick={() => setAttempt((n) => n + 1)}>
            Preview unavailable — retry
          </button>
        </div>
      )}
    </div>
  );
}
