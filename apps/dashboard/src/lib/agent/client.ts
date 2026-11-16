import { apiRequest } from "@/lib/api/client";

import type {
  AgentEventType,
  AgentStatus,
  CrossingLine,
  EventsPage,
  VisionConfig,
  VisionState,
  Zone,
  Camera,
  CameraGroup,
  CameraInput,
  CameraUpdate,
  StreamToken,
  TestResult,
} from "./types";

/**
 * Edge agent (camera manager) base URL. In MVP 02 the dashboard talks to one agent directly;
 * multi-agent routing through the central server arrives with device management (MVP 06).
 */
export const AGENT_BASE_URL = (process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:8100").replace(/\/+$/, "");
const BASE = `${AGENT_BASE_URL}/api/v1`;

export function agentUrl(path: string): string {
  return `${AGENT_BASE_URL}${path}`;
}

export const agent = {
  status: (signal?: AbortSignal) => apiRequest<AgentStatus>("/system/status", { baseUrl: BASE, signal }),
  cameras: (token: string, signal?: AbortSignal) => apiRequest<Camera[]>("/cameras", { baseUrl: BASE, token, signal }),
  camera: (token: string, id: string) => apiRequest<Camera>(`/cameras/${id}`, { baseUrl: BASE, token }),
  createCamera: (token: string, body: CameraInput) =>
    apiRequest<Camera>("/cameras", { baseUrl: BASE, token, method: "POST", body, timeoutMs: 20_000 }),
  updateCamera: (token: string, id: string, body: CameraUpdate) =>
    apiRequest<Camera>(`/cameras/${id}`, { baseUrl: BASE, token, method: "PATCH", body, timeoutMs: 20_000 }),
  deleteCamera: (token: string, id: string) =>
    apiRequest<void>(`/cameras/${id}`, { baseUrl: BASE, token, method: "DELETE", timeoutMs: 15_000 }),
  connect: (token: string, id: string) =>
    apiRequest<Camera>(`/cameras/${id}/connect`, { baseUrl: BASE, token, method: "POST" }),
  disconnect: (token: string, id: string) =>
    apiRequest<Camera>(`/cameras/${id}/disconnect`, { baseUrl: BASE, token, method: "POST", timeoutMs: 15_000 }),
  testCamera: (token: string, id: string) =>
    apiRequest<TestResult>(`/cameras/${id}/test`, { baseUrl: BASE, token, method: "POST", timeoutMs: 45_000 }),
  testInput: (token: string, body: Omit<CameraInput, "name">) =>
    apiRequest<TestResult>("/cameras/test", { baseUrl: BASE, token, method: "POST", body, timeoutMs: 45_000 }),
  streamToken: (token: string, id: string) =>
    apiRequest<StreamToken>(`/cameras/${id}/stream-token`, { baseUrl: BASE, token, method: "POST" }),
  groups: (token: string, signal?: AbortSignal) =>
    apiRequest<CameraGroup[]>("/camera-groups", { baseUrl: BASE, token, signal }),
  createGroup: (token: string, name: string, description?: string) =>
    apiRequest<CameraGroup>("/camera-groups", { baseUrl: BASE, token, method: "POST", body: { name, description } }),
  deleteGroup: (token: string, id: string) =>
    apiRequest<void>(`/camera-groups/${id}`, { baseUrl: BASE, token, method: "DELETE" }),

  // Standard Vision Engine
  vision: (token: string, id: string, signal?: AbortSignal) =>
    apiRequest<{ config: VisionConfig; state: VisionState }>(`/cameras/${id}/vision`, { baseUrl: BASE, token, signal }),
  saveVisionConfig: (token: string, id: string, config: VisionConfig) =>
    apiRequest<VisionConfig>(`/cameras/${id}/vision/config`, { baseUrl: BASE, token, method: "PUT", body: config }),
  zones: (token: string, id: string) => apiRequest<Zone[]>(`/cameras/${id}/zones`, { baseUrl: BASE, token }),
  createZone: (token: string, id: string, z: Pick<Zone, "name" | "role" | "polygon" | "is_enabled">) =>
    apiRequest<Zone>(`/cameras/${id}/zones`, { baseUrl: BASE, token, method: "POST", body: z }),
  deleteZone: (token: string, id: string, zoneId: string) =>
    apiRequest<void>(`/cameras/${id}/zones/${zoneId}`, { baseUrl: BASE, token, method: "DELETE" }),
  lines: (token: string, id: string) => apiRequest<CrossingLine[]>(`/cameras/${id}/lines`, { baseUrl: BASE, token }),
  createLine: (token: string, id: string, l: Pick<CrossingLine, "name" | "a" | "b" | "is_enabled">) =>
    apiRequest<CrossingLine>(`/cameras/${id}/lines`, { baseUrl: BASE, token, method: "POST", body: l }),
  deleteLine: (token: string, id: string, lineId: string) =>
    apiRequest<void>(`/cameras/${id}/lines/${lineId}`, { baseUrl: BASE, token, method: "DELETE" }),

  // Local event log
  events: (
    token: string,
    q: { camera_id?: string; event_type?: AgentEventType[]; since?: string; cursor?: string; limit?: number },
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (q.camera_id) params.set("camera_id", q.camera_id);
    q.event_type?.forEach((t) => params.append("event_type", t));
    if (q.since) params.set("since", q.since);
    if (q.cursor) params.set("cursor", q.cursor);
    params.set("limit", String(q.limit ?? 50));
    return apiRequest<EventsPage>(`/events?${params}`, { baseUrl: BASE, token, signal });
  },
  eventsSummary: (token: string, signal?: AbortSignal) =>
    apiRequest<{ since: string; counts: Partial<Record<AgentEventType, number>> }>("/events/summary", {
      baseUrl: BASE,
      token,
      signal,
    }),
};
