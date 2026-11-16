"use client";

import type { CrossingLine, Zone } from "@/lib/agent/types";

type Pt = [number, number];

/**
 * SVG drawing surface over a camera snapshot. Coordinates are normalized (0..1) so zones and
 * lines stay valid if the camera resolution changes. Keyboard-free mouse/touch editing:
 * click to add points; the parent decides when a shape is complete.
 */
export function GeometryCanvas({
  imageUrl,
  zones,
  lines,
  draft,
  mode,
  onPoint,
}: {
  imageUrl: string | null;
  zones: Zone[];
  lines: CrossingLine[];
  draft: Pt[];
  mode: "zone" | "line" | null;
  onPoint: (p: Pt) => void;
}) {
  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!mode) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    onPoint([Number(x.toFixed(4)), Number(y.toFixed(4))]);
  };

  const pts = (p: Pt[]) => p.map(([x, y]) => `${x * 1000},${y * 1000}`).join(" ");

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-md border bg-black">
      {imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- authenticated snapshot from the edge agent
        <img src={imageUrl} alt="Camera snapshot" className="absolute inset-0 size-full object-fill" />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-xs text-muted-foreground">
          Snapshot unavailable — the camera must be online to draw zones.
        </div>
      )}
      <svg
        viewBox="0 0 1000 1000"
        preserveAspectRatio="none"
        className={mode ? "absolute inset-0 size-full cursor-crosshair" : "absolute inset-0 size-full"}
        onClick={handleClick}
        role="img"
        aria-label="Zones and lines editor"
      >
        {zones.map((z) => (
          <polygon
            key={z.id}
            points={pts(z.polygon)}
            fill={z.role === "exclude" ? "rgba(140,140,140,0.25)" : "rgba(60,190,255,0.18)"}
            stroke={z.role === "exclude" ? "#9a9a9a" : "#3cbeff"}
            strokeWidth={3}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {lines.map((l) => {
          const [ax, ay] = l.a;
          const [bx, by] = l.b;
          const mx = ((ax + bx) / 2) * 1000;
          const my = ((ay + by) / 2) * 1000;
          // forward direction = normal (dy, -dx) of A→B, same convention as the agent
          const dx = (bx - ax) * 1000;
          const dy = (by - ay) * 1000;
          const len = Math.hypot(dx, dy) || 1;
          return (
            <g key={l.id}>
              <line x1={ax * 1000} y1={ay * 1000} x2={bx * 1000} y2={by * 1000} stroke="#ff78ff" strokeWidth={3} vectorEffect="non-scaling-stroke" />
              <line
                x1={mx}
                y1={my}
                x2={mx + (dy / len) * 60}
                y2={my - (dx / len) * 60}
                stroke="#ff78ff"
                strokeWidth={2}
                strokeDasharray="6 4"
                vectorEffect="non-scaling-stroke"
              />
              <circle cx={mx + (dy / len) * 60} cy={my - (dx / len) * 60} r={8} fill="#ff78ff" />
            </g>
          );
        })}
        {draft.length > 0 && (
          <>
            <polyline
              points={pts(draft)}
              fill={mode === "zone" && draft.length > 2 ? "rgba(255,200,60,0.18)" : "none"}
              stroke="#ffc83c"
              strokeWidth={3}
              strokeDasharray="8 5"
              vectorEffect="non-scaling-stroke"
            />
            {draft.map(([x, y], i) => (
              <circle key={i} cx={x * 1000} cy={y * 1000} r={9} fill="#ffc83c" />
            ))}
          </>
        )}
      </svg>
    </div>
  );
}
