"use client";

import { StatusIndicator } from "@/components/common/status-indicator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatTime } from "@/lib/format";
import { useRealtime } from "@/lib/realtime/realtime-provider";

/** WebSocket link to the central server: Live / Reconnecting (with automatic retry). */
export function LiveStatus() {
  const { state, lastMessageAt } = useRealtime();
  const tone =
    state === "live" ? "online" : state === "connecting" ? "warning" : "error";
  const label =
    state === "live"
      ? "Live"
      : state === "connecting"
        ? "Connecting…"
        : "Reconnecting…";
  const detail =
    state === "live"
      ? `Real-time updates active${lastMessageAt ? ` · last ${formatTime(lastMessageAt)}` : ""}`
      : "Real-time connection lost — retrying automatically. Counters and statuses may be out of date.";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-testid="live-status"
          data-state={state}
          className="hidden h-7 items-center rounded-md border bg-background/40 px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex"
          aria-label={`${label}. ${detail}`}
        >
          <StatusIndicator tone={tone} label={label} pulse={state === "live"} />
        </button>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}
