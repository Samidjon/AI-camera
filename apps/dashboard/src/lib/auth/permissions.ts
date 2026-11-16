"use client";

import { useAuth } from "./auth-provider";

export type Permission =
  "cameras:read" | "cameras:control" | "cameras:manage" | (string & {});

/** UI-level permission check. Hides controls only; every action is enforced by the API/agent. */
export function usePermissions() {
  const { state } = useAuth();
  const perms = new Set(
    state.status === "authenticated" ? state.user.role.permissions : [],
  );
  const can = (p: Permission) => perms.has("*") || perms.has(p);
  return { can, token: state.status === "authenticated" ? state.token : null };
}
