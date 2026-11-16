"use client";

import { Database, Loader2, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api/client";
import { platform, type SystemSettings } from "@/lib/api/platform";

/** Server-side retention and privacy (central database). Edge devices have their own retention. */
export function DataPrivacyPanel({
  token,
  canEdit,
}: {
  token: string;
  canEdit: boolean;
}) {
  const [saved, setSaved] = useState<SystemSettings | null>(null);
  const [draft, setDraft] = useState<SystemSettings | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    platform.settings(token).then(
      (s) => {
        setSaved(s);
        setDraft(s);
      },
      (e) =>
        setMessage({
          ok: false,
          text:
            e instanceof ApiError
              ? e.message
              : "Central server is unreachable.",
        }),
    );
  }, [token]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      const s = await platform.updateSettings(token, draft);
      setSaved(s);
      setDraft(s);
      setMessage({
        ok: true,
        text: "Saved. Retention runs hourly on the server.",
      });
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

  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);
  const set = <K extends keyof SystemSettings>(k: K, v: SystemSettings[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  return (
    <Panel
      title="Data retention & privacy (central server)"
      icon={Database}
      actions={
        canEdit && (
          <Button size="sm" onClick={save} disabled={!dirty || busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Save />} Save
          </Button>
        )
      }
    >
      {message && (
        <Alert
          variant={message.ok ? "default" : "destructive"}
          className="mb-3"
        >
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}
      {draft && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ret-events">Keep events (days)</Label>
            <Input
              id="ret-events"
              type="number"
              min={0}
              max={3650}
              disabled={!canEdit}
              value={draft.events_retention_days}
              onChange={(e) =>
                set("events_retention_days", Number(e.target.value))
              }
            />
            <p className="text-xs text-muted-foreground">
              0 = keep forever. Hourly statistics (no personal data) are kept.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ret-obs">Keep person observations (days)</Label>
            <Input
              id="ret-obs"
              type="number"
              min={0}
              max={3650}
              disabled={!canEdit}
              value={draft.observations_retention_days}
              onChange={(e) =>
                set("observations_retention_days", Number(e.target.value))
              }
            />
            <p className="text-xs text-muted-foreground">
              AI person events; usually shorter than other events.
            </p>
          </div>
          <label className="flex items-start justify-between gap-4 rounded-md border p-3">
            <span>
              <span className="text-sm font-medium">
                Store clothing attributes
              </span>
              <span className="block text-xs text-muted-foreground">
                When off, AI clothing attributes are removed from person events
                on arrival and never stored centrally.
              </span>
            </span>
            <Switch
              checked={draft.store_person_attributes}
              disabled={!canEdit}
              onCheckedChange={(v) => set("store_person_attributes", v)}
              aria-label="Store clothing attributes"
            />
          </label>
          <label className="flex items-start justify-between gap-4 rounded-md border p-3">
            <span>
              <span className="text-sm font-medium">
                Show clothing attributes
              </span>
              <span className="block text-xs text-muted-foreground">
                Hide them on the Observations page without deleting them.
              </span>
            </span>
            <Switch
              checked={draft.show_person_attributes}
              disabled={!canEdit}
              onCheckedChange={(v) => set("show_person_attributes", v)}
              aria-label="Show clothing attributes"
            />
          </label>
        </div>
      )}
      <p className="mt-3 text-xs text-muted-foreground">
        Always on: no face recognition, no identification, observations never
        linked across cameras. Edge devices keep their own local retention
        (Devices → configuration, or the fleet settings below).
      </p>
    </Panel>
  );
}
