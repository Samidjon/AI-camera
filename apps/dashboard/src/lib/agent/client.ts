import { apiRequest } from "@/lib/api/client";

import type {
  AgentStatus,
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
};
