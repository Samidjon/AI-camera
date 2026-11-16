"use client";

import { BrainCircuit } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { StatusIndicator, type StatusTone } from "@/components/common/status-indicator";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { AIMode } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";

const TONE: Record<AIMode, StatusTone> = {
  active: "online",
  degraded: "warning",
  loading: "warning",
  recovering: "warning",
  error: "error",
  offline: "idle",
  disabled: "idle",
};

const LABEL: Record<AIMode, string> = {
  active: "AI on",
  degraded: "AI degraded",
  loading: "AI loading",
  recovering: "AI recovering",
  error: "AI error",
  offline: "AI offline",
  disabled: "AI off",
};

/** Always-visible AI state; links to the AI Engine page where it is switched on / off. */
export function AiStatusChip() {
  const { token } = usePermissions();
  const fetcher = useCallback(
    (signal: AbortSignal) => (token ? agent.aiStatus(token, signal) : Promise.reject(new Error("Not signed in"))),
    [token],
  );
  const { data, error } = usePolling(fetcher, 10_000);
  if (error || !data) return null;
  return (
    <Link
      href="/ai"
      className="hidden h-7 items-center gap-1.5 rounded-md border bg-background/40 px-2.5 outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring md:flex"
      title={data.reason}
    >
      <BrainCircuit className="size-3.5 text-muted-foreground" aria-hidden />
      <StatusIndicator tone={TONE[data.mode]} label={LABEL[data.mode]} />
    </Link>
  );
}
