"use client";

import { useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api/client";

export interface ServerMessage {
  type:
    | "device.status"
    | "camera.status"
    | "event.created"
    | "sync.status"
    | "system.notification"
    | string;
  data: Record<string, unknown>;
  at?: string;
}

export type LiveState = "connecting" | "live" | "offline";

const WS_URL = `${API_BASE_URL.replace(/^http/, "ws")}/api/v1/ws`;

/**
 * Live updates from the central server over WebSocket. Authenticates with the first message
 * (the token never goes into the URL), reconnects with exponential backoff (1 s → 30 s).
 */
export function useServerEvents(
  token: string | null,
  onMessage: (m: ServerMessage) => void,
): LiveState {
  const [state, setState] = useState<LiveState>("connecting");
  const handler = useRef(onMessage);

  useEffect(() => {
    handler.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    if (!token) return;
    let ws: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      setState("connecting");
      ws = new WebSocket(WS_URL);
      ws.onopen = () => ws?.send(JSON.stringify({ type: "auth", token }));
      ws.onmessage = (ev) => {
        let msg: ServerMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "welcome") {
          retry = 0;
          setState("live");
        } else if (
          msg.type !== "ping" &&
          msg.type !== "pong" &&
          msg.type !== "error"
        ) {
          handler.current(msg);
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        setState("offline");
        const delay =
          Math.min(30_000, 1000 * 2 ** retry) * (0.5 + Math.random() / 2);
        retry = Math.min(retry + 1, 6);
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [token]);

  return token ? state : "offline";
}
