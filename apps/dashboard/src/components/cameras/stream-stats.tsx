import type { Camera } from "@/lib/agent/types";

/** "1280×720 · 24.8 fps" — measured FPS when streaming; "—" when unknown (never invented). */
export function StreamStats({ camera }: { camera: Camera }) {
  const res = camera.resolution ? `${camera.resolution.width}×${camera.resolution.height}` : "—";
  const fps = camera.fps != null ? `${camera.fps.toFixed(1)} fps` : "— fps";
  return (
    <span className="tabular">
      {res} · {fps}
    </span>
  );
}
