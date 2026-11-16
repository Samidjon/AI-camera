"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AGENT_BASE_URL } from "./client";

export type StreamState = "connecting" | "live" | "offline" | "unauthorized";

export interface LiveCameraStatus {
  status: string;
  fps: number | null;
  error: string | null;
  width: number | null;
  height: number | null;
}

export interface StreamOptions {
  layers: string[];
  maxFps: number;
  maxWidth: number;
}

const LIVE_URL = `${AGENT_BASE_URL.replace(/^http/, "ws")}/api/v1/live`;

/**
 * Multiplexed live video from the edge agent: every camera of the wall shares ONE WebSocket
 * (no per-camera HTTP connections, no polling). Binary messages = `uint32 header length | JSON
 * header | JPEG`; frames are decoded with createImageBitmap and painted onto a canvas per camera.
 */
export function useLiveStream(
  token: string | null,
  cameraIds: string[],
  opts: StreamOptions,
) {
  const [state, setState] = useState<StreamState>("connecting");
  const [statuses, setStatuses] = useState<Record<string, LiveCameraStatus>>(
    {},
  );
  const [received, setReceived] = useState<Record<string, number>>({}); // frames/s actually displayed
  const canvases = useRef(new Map<string, HTMLCanvasElement>());
  const counters = useRef(new Map<string, number>());
  const wsRef = useRef<WebSocket | null>(null);
  const subRef = useRef({ cameraIds, opts });
  const key = `${cameraIds.join(",")}|${opts.layers.join(",")}|${opts.maxFps}|${opts.maxWidth}`;

  const sendSubscription = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const { cameraIds: ids, opts: o } = subRef.current;
    ws.send(
      JSON.stringify({
        type: "subscribe",
        cameras: ids,
        layers: o.layers.join(","),
        max_fps: o.maxFps,
        max_width: o.maxWidth,
      }),
    );
  }, []);

  useEffect(() => {
    subRef.current = { cameraIds, opts };
    sendSubscription();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures ids/options by value
  }, [key, sendSubscription]);

  useEffect(() => {
    if (!token) return;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let socket: WebSocket | null = null; // this effect run's socket (React may run effects twice)

    const draw = async (data: ArrayBuffer) => {
      const view = new DataView(data);
      const hlen = view.getUint32(0);
      const header = JSON.parse(
        new TextDecoder().decode(new Uint8Array(data, 4, hlen)),
      ) as { camera_id: string };
      const canvas = canvases.current.get(header.camera_id);
      if (!canvas) return;
      const bitmap = await createImageBitmap(
        new Blob([new Uint8Array(data, 4 + hlen)], { type: "image/jpeg" }),
      );
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
      }
      canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
      bitmap.close();
      counters.current.set(
        header.camera_id,
        (counters.current.get(header.camera_id) ?? 0) + 1,
      );
      canvas.dataset.frames = String(Number(canvas.dataset.frames ?? 0) + 1);
    };

    const connect = () => {
      setState("connecting");
      const ws = new WebSocket(LIVE_URL);
      ws.binaryType = "arraybuffer";
      socket = ws;
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          void draw(ev.data as ArrayBuffer).catch(() => undefined);
          return;
        }
        const msg = JSON.parse(ev.data) as {
          type: string;
          cameras?: Record<string, LiveCameraStatus>;
        };
        if (msg.type === "welcome") {
          retry = 0;
          setState("live");
          sendSubscription();
        } else if (msg.type === "status" && msg.cameras) {
          setStatuses(msg.cameras);
        } else if (msg.type === "error") {
          stopped = true; // bad token / permission: don't hammer the agent
          setState("unauthorized");
        }
      };
      ws.onclose = () => {
        // A late close of an older socket must not detach the current one.
        if (wsRef.current === ws) wsRef.current = null;
        if (stopped) return;
        setState("offline");
        const delay =
          Math.min(15_000, 1000 * 2 ** retry) * (0.5 + Math.random() / 2);
        retry = Math.min(retry + 1, 5);
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    const meter = setInterval(() => {
      setReceived(Object.fromEntries(counters.current));
      counters.current.clear();
    }, 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(meter);
      socket?.close();
    };
  }, [token, sendSubscription]);

  /** Ref callback for a camera tile's canvas. */
  const bindCanvas = useCallback(
    (cameraId: string) => (el: HTMLCanvasElement | null) => {
      if (el) canvases.current.set(cameraId, el);
      else canvases.current.delete(cameraId);
    },
    [],
  );

  return { state: token ? state : "offline", statuses, received, bindCanvas };
}
