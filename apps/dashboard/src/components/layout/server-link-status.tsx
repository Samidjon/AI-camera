"use client";

import { StatusIndicator } from "@/components/common/status-indicator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatTime } from "@/lib/format";
import { useSystemStatus } from "@/lib/system/system-status-provider";

/** Central-server link state. Never shows "online" when the last poll failed. */
export function ServerLinkStatus() {
  const { data, error, loading, updatedAt } = useSystemStatus();

  let tone: "online" | "warning" | "error" | "idle" = "idle";
  let label = "Checking…";
  let detail = "Contacting central server";

  if (!loading) {
    if (error) {
      tone = "error";
      label = "Server unreachable";
      detail = updatedAt
        ? `Last successful update ${formatTime(updatedAt)} — data shown may be stale`
        : "No data received from the central server yet";
    } else if (data) {
      tone =
        data.status === "ok"
          ? "online"
          : data.status === "degraded"
            ? "warning"
            : "error";
      label =
        data.status === "ok"
          ? "Server online"
          : data.status === "degraded"
            ? "Server degraded"
            : "Server error";
      detail = `Updated ${formatTime(updatedAt)}`;
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="flex h-7 items-center rounded-md border bg-background/40 px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`${label}. ${detail}`}
        >
          <StatusIndicator
            tone={tone}
            label={label}
            pulse={tone === "online"}
          />
        </button>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}
