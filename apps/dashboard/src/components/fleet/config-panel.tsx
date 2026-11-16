"use client";

import { History, Loader2, RotateCcw, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api/client";
import {
  fleet,
  type ConfigState,
  type ConfigVersion,
  type DeviceConfigDoc,
  type FleetCamera,
} from "@/lib/api/fleet";
import { formatDateTime } from "@/lib/format";

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  applied: "default",
  partial: "secondary",
  failed: "destructive",
  pending: "outline",
  superseded: "outline",
};

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

/** Versioned remote configuration: edit → new version → device applies and acknowledges. */
export function ConfigPanel({
  token,
  deviceId,
  cameras,
  canManage,
  revision,
}: {
  token: string;
  deviceId: string;
  cameras: FleetCamera[];
  canManage: boolean;
  revision: number;
}) {
  const [state, setState] = useState<ConfigState | null>(null);
  const [history, setHistory] = useState<ConfigVersion[]>([]);
  const [draft, setDraft] = useState<DeviceConfigDoc>({});
  const [json, setJson] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    const [s, h] = await Promise.all([
      fleet.config(token, deviceId),
      fleet.configHistory(token, deviceId),
    ]);
    setState(s);
    setHistory(h);
    setDraft(clone(s.config));
    setJson(JSON.stringify(s.config, null, 2));
  }, [token, deviceId]);

  useEffect(() => {
    void Promise.resolve()
      .then(load)
      .catch((e) =>
        setMessage({
          ok: false,
          text:
            e instanceof ApiError
              ? e.message
              : "Central server is unreachable.",
        }),
      );
  }, [load, revision]);

  async function save(doc: DeviceConfigDoc) {
    if (!state) return;
    setBusy(true);
    setMessage(null);
    try {
      const s = await fleet.patchConfig(token, deviceId, {
        base_version: state.version,
        config: doc,
        replace: true,
        comment: comment || undefined,
      });
      setMessage({
        ok: true,
        text:
          s.version === state.version
            ? "No changes."
            : `Version ${s.version} queued — the device applies it at its next heartbeat.`,
      });
      setComment("");
      await load();
    } catch (e) {
      const conflict =
        e instanceof ApiError && e.code === "configuration_conflict";
      setMessage({
        ok: false,
        text: conflict
          ? `${e.message}`
          : e instanceof ApiError
            ? e.message
            : "Central server is unreachable.",
      });
      if (conflict) await load();
    } finally {
      setBusy(false);
    }
  }

  async function rollback(to: number) {
    if (!state) return;
    setBusy(true);
    setMessage(null);
    try {
      const s = await fleet.rollbackConfig(token, deviceId, to, state.version);
      setMessage({
        ok: true,
        text: `Rolled back: version ${s.version} has the content of v${to}.`,
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

  const cam = (id: string) => draft.cameras?.[id] ?? {};
  const setCam = (
    id: string,
    patch: NonNullable<DeviceConfigDoc["cameras"]>[string],
  ) =>
    setDraft((d) => ({
      ...d,
      cameras: {
        ...(d.cameras ?? {}),
        [id]: { ...(d.cameras?.[id] ?? {}), ...patch },
      },
    }));

  if (!state)
    return (
      <p className="text-sm text-muted-foreground">Loading configuration…</p>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>Desired v{state.version}</span>
        <span className="text-muted-foreground">·</span>
        <span>Applied v{state.applied_version}</span>
        {state.status && (
          <Badge variant={STATUS_VARIANT[state.status] ?? "outline"}>
            {state.status}
          </Badge>
        )}
        {state.ack.errors && Object.keys(state.ack.errors).length > 0 && (
          <span className="text-xs text-destructive">
            {Object.entries(state.ack.errors)
              .map(([k, v]) => `${k}: ${v}`)
              .join("; ")}
          </span>
        )}
      </div>

      {message && (
        <Alert variant={message.ok ? "default" : "destructive"}>
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}

      {canManage && !advanced && (
        <div className="flex flex-col gap-3 rounded-md border p-3">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="cfg-ai">AI analysis (whole device)</Label>
            <Switch
              id="cfg-ai"
              checked={draft.ai_enabled ?? false}
              onCheckedChange={(v) =>
                setDraft((d) => ({ ...d, ai_enabled: v }))
              }
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="cfg-ret">Event retention (days)</Label>
            <Input
              id="cfg-ret"
              type="number"
              className="w-24"
              min={1}
              max={3650}
              value={draft.events?.retention_days ?? ""}
              placeholder="local"
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  events: {
                    ...(d.events ?? {}),
                    retention_days: e.target.value
                      ? Number(e.target.value)
                      : undefined,
                  },
                }))
              }
            />
          </div>
          {cameras.length > 0 && (
            <p className="pt-1 text-xs font-medium text-muted-foreground uppercase">
              Cameras
            </p>
          )}
          {cameras.map((c) => {
            const id = c.external_id ?? c.id;
            const s = cam(id);
            return (
              <div
                key={id}
                className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 text-sm"
              >
                <span className="truncate">{c.name}</span>
                <label className="flex items-center gap-1.5 text-xs">
                  On
                  <Switch
                    checked={s.is_enabled ?? c.is_enabled}
                    onCheckedChange={(v) => setCam(id, { is_enabled: v })}
                    aria-label={`${c.name} enabled`}
                  />
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  AI
                  <Switch
                    checked={s.ai_enabled ?? c.ai_enabled ?? true}
                    onCheckedChange={(v) => setCam(id, { ai_enabled: v })}
                    aria-label={`${c.name} AI`}
                  />
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  Sensitivity
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    className="h-7 w-16"
                    value={s.motion?.sensitivity ?? ""}
                    placeholder="—"
                    aria-label={`${c.name} motion sensitivity`}
                    onChange={(e) =>
                      setCam(id, {
                        motion: {
                          ...(s.motion ?? {}),
                          sensitivity: e.target.value
                            ? Number(e.target.value)
                            : undefined,
                        },
                      })
                    }
                  />
                </label>
              </div>
            );
          })}
        </div>
      )}

      {canManage && advanced && (
        <textarea
          className="min-h-56 rounded-md border bg-background p-3 font-mono text-xs"
          value={json}
          spellCheck={false}
          aria-label="Configuration JSON"
          onChange={(e) => setJson(e.target.value)}
        />
      )}

      {canManage && (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="h-8 flex-1"
            placeholder="Change note (optional)"
            value={comment}
            maxLength={300}
            onChange={(e) => setComment(e.target.value)}
          />
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              if (!advanced) return void save(draft);
              try {
                void save(JSON.parse(json));
              } catch {
                setMessage({ ok: false, text: "The JSON is not valid." });
              }
            }}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save as new version
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setAdvanced((a) => !a)}
          >
            {advanced ? "Simple editor" : "JSON editor"}
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase">
          <History className="size-3.5" /> History
        </p>
        {history.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No configuration pushed yet — the device uses its local settings.
          </p>
        )}
        {history.map((h) => (
          <div
            key={h.version}
            className="flex items-center gap-2 border-b py-1.5 text-xs last:border-0"
          >
            <span className="w-8 font-mono">v{h.version}</span>
            <Badge variant={STATUS_VARIANT[h.status] ?? "outline"}>
              {h.status}
            </Badge>
            <span className="flex-1 truncate text-muted-foreground">
              {h.rollback_of
                ? `rollback to v${h.rollback_of}`
                : (h.comment ?? "")}
            </span>
            <span className="text-muted-foreground">
              {formatDateTime(h.created_at)}
            </span>
            {canManage && h.version !== state.version && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2"
                disabled={busy}
                onClick={() => void rollback(h.version)}
              >
                <RotateCcw className="size-3.5" /> Restore
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
