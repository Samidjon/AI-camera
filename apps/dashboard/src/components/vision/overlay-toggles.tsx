"use client";

import { cn } from "@/lib/utils";

export const OVERLAY_LAYERS = [
  { id: "boxes", label: "Boxes" },
  { id: "regions", label: "Motion regions" },
  { id: "tracks", label: "Track IDs" },
  { id: "zones", label: "Zones" },
  { id: "lines", label: "Lines" },
] as const;

/** Toggle chips for agent-drawn overlays. Each layer is independent. */
export function OverlayToggles({ value, onChange }: { value: string[]; onChange: (layers: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Overlay layers">
      {OVERLAY_LAYERS.map((l) => {
        const on = value.includes(l.id);
        return (
          <button
            key={l.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((v) => v !== l.id) : [...value, l.id])}
            className={cn(
              "h-6 rounded-full border px-2.5 text-[11px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
              on ? "border-primary/50 bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {l.label}
          </button>
        );
      })}
    </div>
  );
}
