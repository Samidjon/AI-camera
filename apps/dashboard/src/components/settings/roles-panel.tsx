"use client";

import { Lock, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { platform, type RoleInfo } from "@/lib/api/platform";

/** Permission matrix. The administrator role is fixed ("*"); operator / viewer are editable. */
export function RolesPanel({ token }: { token: string }) {
  const [catalog, setCatalog] = useState<Record<string, string>>({});
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([
        platform.permissionCatalog(token),
        platform.roles(token),
      ]);
      setCatalog(c);
      setRoles(r);
      setDraft(Object.fromEntries(r.map((x) => [x.name, [...x.permissions]])));
    } catch (e) {
      setMessage({
        ok: false,
        text:
          e instanceof ApiError ? e.message : "Central server is unreachable.",
      });
    }
  }, [token]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  const has = (role: RoleInfo, p: string) =>
    (draft[role.name] ?? []).includes("*") ||
    (draft[role.name] ?? []).includes(p);
  const changed = (role: RoleInfo) =>
    [...(draft[role.name] ?? [])].sort().join() !==
    [...role.permissions].sort().join();

  async function save(role: RoleInfo) {
    setBusy(true);
    setMessage(null);
    try {
      await platform.updateRole(token, role.name, draft[role.name] ?? []);
      setMessage({
        ok: true,
        text: `Permissions of ${role.name} saved. The server applies them immediately; edge agents at the users' next sign-in.`,
      });
      await load();
    } catch (e) {
      setMessage({
        ok: false,
        text:
          e instanceof ApiError ? e.message : "Central server is unreachable.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Roles and permissions" icon={ShieldCheck} bodyClassName="p-0">
      {message && (
        <Alert
          variant={message.ok ? "default" : "destructive"}
          className="m-3 w-auto"
        >
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="roles-matrix">
          <thead>
            <tr className="border-b">
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                Permission
              </th>
              {roles.map((r) => (
                <th key={r.name} className="px-3 py-2 text-center font-medium">
                  <div className="capitalize">{r.name}</div>
                  <div className="text-[10px] font-normal text-muted-foreground">
                    {r.user_count} users
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(catalog).map(([perm, description]) => (
              <tr key={perm} className="border-b last:border-0">
                <td className="px-4 py-1.5">
                  <div className="font-mono text-xs">{perm}</div>
                  <div className="text-xs text-muted-foreground">
                    {description}
                  </div>
                </td>
                {roles.map((r) => {
                  const locked = !r.editable || perm === "users:manage";
                  return (
                    <td key={r.name} className="px-3 text-center">
                      {locked ? (
                        has(r, perm) ? (
                          <Lock
                            className="mx-auto size-3.5 text-muted-foreground"
                            aria-label="granted (fixed)"
                          />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )
                      ) : (
                        <input
                          type="checkbox"
                          className="size-4 accent-[var(--primary)]"
                          aria-label={`${r.name}: ${perm}`}
                          checked={has(r, perm)}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              [r.name]: e.target.checked
                                ? [...(d[r.name] ?? []), perm]
                                : (d[r.name] ?? []).filter((x) => x !== perm),
                            }))
                          }
                        />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td className="px-4 py-2 text-xs text-muted-foreground">
                Administrator always has every permission.
              </td>
              {roles.map((r) => (
                <td key={r.name} className="px-3 py-2 text-center">
                  {r.editable && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || !changed(r)}
                      onClick={() => void save(r)}
                    >
                      <Save /> Save
                    </Button>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
