"use client";

import { createContext, useContext } from "react";

import { usePolling, type PollingResult } from "@/hooks/use-polling";
import { api } from "@/lib/api/client";
import type { SystemStatus } from "@/lib/api/types";

const POLL_INTERVAL_MS = 15_000;

const SystemStatusContext = createContext<PollingResult<SystemStatus> | null>(null);

/** Single shared poller for /system/status so the top bar and pages don't duplicate requests. */
export function SystemStatusProvider({ children }: { children: React.ReactNode }) {
  const status = usePolling((signal) => api.systemStatus(signal), POLL_INTERVAL_MS);
  return <SystemStatusContext.Provider value={status}>{children}</SystemStatusContext.Provider>;
}

export function useSystemStatus(): PollingResult<SystemStatus> {
  const ctx = useContext(SystemStatusContext);
  if (!ctx) throw new Error("useSystemStatus must be used inside <SystemStatusProvider>");
  return ctx;
}
