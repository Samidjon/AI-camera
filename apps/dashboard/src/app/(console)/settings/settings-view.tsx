"use client";

import { ExternalLink, KeyRound, Link2, UserRound } from "lucide-react";

import { ModulePending } from "@/components/common/module-pending";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { Badge } from "@/components/ui/badge";
import { AGENT_BASE_URL } from "@/lib/agent/client";
import { API_BASE_URL } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import { formatDateTime } from "@/lib/format";

export function SettingsView() {
  const { state } = useAuth();
  const user = state.status === "authenticated" ? state.user : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Settings" description="Account, connection and system configuration" />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Account" icon={UserRound}>
          {user ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted-foreground">Email</dt>
              <dd className="truncate">{user.email}</dd>
              <dt className="text-muted-foreground">Name</dt>
              <dd>{user.full_name ?? "—"}</dd>
              <dt className="text-muted-foreground">Role</dt>
              <dd>
                <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                  {user.role.name}
                </Badge>
              </dd>
              <dt className="text-muted-foreground">Last sign-in</dt>
              <dd>{formatDateTime(user.last_login_at)}</dd>
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground">Not signed in.</p>
          )}
        </Panel>

        <Panel title="Central server" icon={Link2}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-muted-foreground">API endpoint</dt>
            <dd className="truncate font-mono text-xs leading-5">{API_BASE_URL}/api/v1</dd>
            <dt className="text-muted-foreground">Edge agent</dt>
            <dd className="truncate font-mono text-xs leading-5">{AGENT_BASE_URL}/api/v1</dd>
            <dt className="text-muted-foreground">Documentation</dt>
            <dd>
              <a
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
              >
                OpenAPI / Swagger <ExternalLink className="size-3" aria-hidden />
              </a>
            </dd>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            Configured with <span className="font-mono">NEXT_PUBLIC_API_URL</span> and{" "}
            <span className="font-mono">NEXT_PUBLIC_AGENT_URL</span>.
          </p>
        </Panel>

        <Panel title="Permissions" icon={KeyRound} className="lg:col-span-2">
          {user ? (
            <div className="flex flex-wrap gap-1.5">
              {user.role.permissions.map((p) => (
                <Badge key={p} variant="outline" className="font-mono text-[11px]">
                  {p === "*" ? "* (all permissions)" : p}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">—</p>
          )}
        </Panel>
      </div>

      <ModulePending
        mvp="MVP 07"
        title="Administration"
        description="User and role management, retention and privacy settings are built on top of the existing users/roles tables in MVP 07. Until then, users are created with the server CLI."
        planned={[
          "User management",
          "Roles and permissions editor",
          "Camera, device and AI settings",
          "Data retention",
          "Privacy settings",
          "Light / dark theme switch",
        ]}
      />
    </div>
  );
}
