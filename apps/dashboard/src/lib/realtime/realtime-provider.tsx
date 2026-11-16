"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import { usePermissions } from "@/lib/auth/permissions";

import {
  useServerEvents,
  type LiveState,
  type ServerMessage,
} from "./use-server-events";

type Listener = (m: ServerMessage) => void;

interface RealtimeContextValue {
  state: LiveState;
  /** When the last message arrived (null = none yet). */
  lastMessageAt: Date | null;
  subscribe: (fn: Listener) => () => void;
}

const RealtimeContext = createContext<RealtimeContextValue | null>(null);

/** One WebSocket to the central server for the whole console; pages subscribe to its messages. */
export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const { token } = usePermissions();
  const listeners = useRef(new Set<Listener>());
  const [lastMessageAt, setLast] = useState<Date | null>(null);

  const onMessage = useCallback((m: ServerMessage) => {
    setLast(new Date());
    for (const fn of listeners.current) {
      try {
        fn(m);
      } catch {
        /* one failing listener must not stop the others */
      }
    }
  }, []);
  const state = useServerEvents(token, onMessage);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  const value = useMemo(
    () => ({ state, lastMessageAt, subscribe }),
    [state, lastMessageAt, subscribe],
  );
  return (
    <RealtimeContext.Provider value={value}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime(): RealtimeContextValue {
  const ctx = useContext(RealtimeContext);
  if (!ctx)
    throw new Error("useRealtime must be used inside <RealtimeProvider>");
  return ctx;
}
