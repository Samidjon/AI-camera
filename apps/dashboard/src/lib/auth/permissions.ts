"use client";

import { useCallback, useMemo } from "react";

import { useAuth } from "./auth-provider";

export type Permission =
  "cameras:read" | "cameras:control" | "cameras:manage" | (string & {});

/** UI-level permission check. Hides controls only; every action is enforced by the API/agent.
 *  `can` is stable while the role is unchanged, so it is safe in effect dependency lists. */
export function usePermissions() {
  const { state } = useAuth();
  const list =
    state.status === "authenticated" ? state.user.role.permissions : null;
  const key = list ? list.join(",") : "";
  const perms = useMemo(() => new Set(key ? key.split(",") : []), [key]);
  const can = useCallback(
    (p: Permission) => perms.has("*") || perms.has(p),
    [perms],
  );
  return { can, token: state.status === "authenticated" ? state.token : null };
}
