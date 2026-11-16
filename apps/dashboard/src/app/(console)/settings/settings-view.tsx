"use client";

import { ExternalLink, KeyRound, Link2, Server, UserRound } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { DataPrivacyPanel } from "@/components/settings/data-privacy-panel";
import { FleetSettingsPanel } from "@/components/settings/fleet-settings-panel";
import { RolesPanel } from "@/components/settings/roles-panel";
import { UsersPanel } from "@/components/settings/users-panel";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AGENT_BASE_URL } from "@/lib/agent/client";
import { API_BASE_URL } from "@/lib/api/client";
import { platform } from "@/lib/api/platform";
import { useAuth } from "@/lib/auth/auth-provider";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";

function ConfigTree({ value }: { value: unknown }) {
  if (value === null || typeof value !== "object") {
    return <span className="font-mono text-xs">{String(value)}</span>;
  }
  return (
    <dl className="grid grid-cols-[minmax(10rem,auto)_1fr] gap-x-4 gap-y-1">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-xs text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-all">
            {Array.isArray(v) ? (
              <span className="font-mono text-xs">{v.join(", ")}</span>
            ) : (
              <ConfigTree value={v} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SystemPanel({ token }: { token: string }) {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    platform
      .configuration(token)
      .then(setConfig, () => setError("Central server is unreachable."));
  }, [token]);
  return (
    <Panel
      title="System configuration (central server, read-only)"
      icon={Server}
    >
      {error && <p className="text-sm text-muted-foreground">{error}</p>}
      {config && <ConfigTree value={config} />}
      <p className="mt-3 text-xs text-muted-foreground">
        Set with <span className="font-mono">ACI_*</span> environment variables
        on the server (see docs/INSTALLATION.md). Secrets are never shown.
      </p>
    </Panel>
  );
}

export function SettingsView() {
  const { state } = useAuth();
  const { token, can } = usePermissions();
  const user = state.status === "authenticated" ? state.user : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Settings"
        description="Account, users and roles, data retention and privacy, fleet and system configuration"
      />
      <Tabs defaultValue="account" className="gap-4">
        <TabsList className="flex-wrap">
          <TabsTrigger value="account">Account</TabsTrigger>
          {can("users:manage") && (
            <TabsTrigger value="users">Users</TabsTrigger>
          )}
          {can("users:manage") && (
            <TabsTrigger value="roles">Roles & permissions</TabsTrigger>
          )}
          <TabsTrigger value="data">Data & privacy</TabsTrigger>
          {can("devices:manage") && (
            <TabsTrigger value="fleet">Cameras, devices, AI</TabsTrigger>
          )}
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>

        <TabsContent value="account">
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
                    <Badge
                      variant="secondary"
                      className="font-mono text-[10px] uppercase"
                    >
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
            <Panel title="Connections" icon={Link2}>
              <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
                <dt className="text-muted-foreground">Central server</dt>
                <dd className="truncate font-mono text-xs leading-5">
                  {API_BASE_URL}/api/v1
                </dd>
                <dt className="text-muted-foreground">Edge agent</dt>
                <dd className="truncate font-mono text-xs leading-5">
                  {AGENT_BASE_URL}/api/v1
                </dd>
                <dt className="text-muted-foreground">API documentation</dt>
                <dd>
                  <a
                    href={`${API_BASE_URL}/docs`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                  >
                    OpenAPI / Swagger{" "}
                    <ExternalLink className="size-3" aria-hidden />
                  </a>
                </dd>
              </dl>
            </Panel>
            <Panel
              title="Your permissions"
              icon={KeyRound}
              className="lg:col-span-2"
            >
              {user && (
                <div className="flex flex-wrap gap-1.5">
                  {user.role.permissions.map((p) => (
                    <Badge
                      key={p}
                      variant="outline"
                      className="font-mono text-[11px]"
                    >
                      {p === "*" ? "* (all permissions)" : p}
                    </Badge>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        </TabsContent>

        {token && can("users:manage") && (
          <TabsContent value="users">
            <UsersPanel token={token} />
          </TabsContent>
        )}
        {token && can("users:manage") && (
          <TabsContent value="roles">
            <RolesPanel token={token} />
          </TabsContent>
        )}
        {token && (
          <TabsContent value="data">
            <DataPrivacyPanel token={token} canEdit={can("settings:manage")} />
          </TabsContent>
        )}
        {token && can("devices:manage") && (
          <TabsContent value="fleet" className="flex flex-col gap-4">
            <FleetSettingsPanel token={token} />
            <p className="text-sm text-muted-foreground">
              Per-camera configuration (zones, lines, detector) is on the
              Cameras page of an agent; a single device&apos;s full
              configuration with history and rollback is on Devices → device →
              Configuration. AI model, thresholds and recovery are on the AI
              Engine page.
            </p>
          </TabsContent>
        )}
        {token && (
          <TabsContent value="system">
            <SystemPanel token={token} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
