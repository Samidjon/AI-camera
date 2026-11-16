"use client";

import { useEffect, useRef } from "react";

import { useRealtime } from "./realtime-provider";
import type { ServerMessage } from "./use-server-events";

/** Run `handler` for every real-time message of the given types (latest handler is always used). */
export function useLiveMessages(
  types: string[] | null,
  handler: (m: ServerMessage) => void,
) {
  const { subscribe } = useRealtime();
  const ref = useRef(handler);
  const key = types?.join(",") ?? "*";
  useEffect(() => {
    ref.current = handler;
  }, [handler]);
  useEffect(() => {
    const wanted = key === "*" ? null : new Set(key.split(","));
    return subscribe((m) => {
      if (!wanted || wanted.has(m.type)) ref.current(m);
    });
  }, [subscribe, key]);
}
