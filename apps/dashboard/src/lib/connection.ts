/**
 * Where the dashboard connects to. Build-time defaults come from NEXT_PUBLIC_API_URL /
 * NEXT_PUBLIC_AGENT_URL; the user can override both on the sign-in page (stored in this browser).
 * This lets one deployed dashboard (e.g. on Vercel, or installed on a phone) connect to any
 * installation — including servers behind tunnels whose address changes.
 */

const API_KEY = "aci.api_url";
const AGENT_KEY = "aci.agent_url";

export const DEFAULT_API_URL = clean(
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
);
export const DEFAULT_AGENT_URL = clean(
  process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:8100",
);

function clean(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function stored(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Validates and normalizes a user-entered address ("server.example.com" → https://server.example.com). */
export function normalizeUrl(input: string): string {
  let v = clean(input);
  if (!v) throw new Error("Enter an address");
  if (!/^https?:\/\//i.test(v)) v = `https://${v}`;
  const u = new URL(v); // throws on garbage
  if (u.username || u.password)
    throw new Error("Addresses must not contain credentials");
  return `${u.protocol}//${u.host}${u.pathname === "/" ? "" : u.pathname.replace(/\/+$/, "")}`;
}

export const apiBaseUrl = (): string => stored(API_KEY) ?? DEFAULT_API_URL;
export const agentBaseUrl = (): string =>
  stored(AGENT_KEY) ?? DEFAULT_AGENT_URL;

/** True when the user saved an address (vs. the build default). */
export const hasCustomConnection = (): boolean => stored(API_KEY) !== null;

export function saveConnection(api: string, agent: string | null): void {
  localStorage.setItem(API_KEY, normalizeUrl(api));
  if (agent && agent.trim())
    localStorage.setItem(AGENT_KEY, normalizeUrl(agent));
  else localStorage.removeItem(AGENT_KEY);
}

export function resetConnection(): void {
  localStorage.removeItem(API_KEY);
  localStorage.removeItem(AGENT_KEY);
}

/** A page served over HTTPS can't call plain-http hosts other than localhost (mixed content). */
export function mixedContentProblem(url: string): boolean {
  if (typeof window === "undefined" || window.location.protocol !== "https:")
    return false;
  try {
    const u = new URL(url);
    return (
      u.protocol === "http:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)
    );
  } catch {
    return false;
  }
}
