"use client";

import { useCallback } from "react";

import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { Camera, CameraGroup } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";

const STATUS_POLL_MS = 3_000;

/**
 * Camera list + groups from the edge agent. Status metadata is polled (small JSON every 3s);
 * video never is — previews use the agent's MJPEG stream. Real-time push via WebSocket
 * replaces this polling in MVP 06/07.
 */
export function useCameras() {
  const { token } = usePermissions();
  const fetchAll = useCallback(
    async (signal: AbortSignal): Promise<{ cameras: Camera[]; groups: CameraGroup[] }> => {
      if (!token) return { cameras: [], groups: [] };
      const [cameras, groups] = await Promise.all([agent.cameras(token, signal), agent.groups(token, signal)]);
      return { cameras, groups };
    },
    [token],
  );
  const result = usePolling(fetchAll, STATUS_POLL_MS);
  return {
    cameras: result.data?.cameras ?? [],
    groups: result.data?.groups ?? [],
    loaded: result.data !== null,
    loading: result.loading,
    error: result.error,
    updatedAt: result.updatedAt,
    refresh: result.refresh,
  };
}
