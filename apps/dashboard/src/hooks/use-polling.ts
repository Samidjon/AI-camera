"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface PollingResult<T> {
  data: T | null;
  error: Error | null;
  /** True until the first response (success or failure) arrives. */
  loading: boolean;
  /** When `data` was last refreshed successfully. Used to flag stale data while offline. */
  updatedAt: Date | null;
  refresh: () => void;
}

/**
 * Polls a JSON resource. Used for low-frequency system metadata (health/status) only —
 * live video and events will use streaming/WebSocket transports (MVP 02 / MVP 06).
 * On failure the last good `data` is kept, so the UI can show it as stale instead of blank.
 */
export function usePolling<T>(fetcher: (signal: AbortSignal) => Promise<T>, intervalMs: number): PollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    let controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    const run = async () => {
      controller = new AbortController();
      try {
        const result = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setData(result);
        setError(null);
        setUpdatedAt(new Date());
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setError(err as Error);
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(run, intervalMs);
        }
      }
    };
    void run();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs, tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, updatedAt, refresh };
}
