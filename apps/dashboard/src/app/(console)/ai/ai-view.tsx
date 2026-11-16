"use client";

import { BrainCircuit, Check, Cpu, Gauge, Loader2, MemoryStick, ShieldCheck, Timer, Users, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { MetricTile } from "@/components/common/metric-tile";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { StatusIndicator, type StatusTone } from "@/components/common/status-indicator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { AIConfig, AIMode, AIModel, AttributeProviderKind } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";

const MODE: Record<AIMode, { label: string; tone: StatusTone }> = {
  active: { label: "Active", tone: "online" },
  degraded: { label: "Degraded", tone: "warning" },
  loading: { label: "Loading", tone: "warning" },
  recovering: { label: "Recovering", tone: "warning" },
  error: { label: "Error", tone: "error" },
  offline: { label: "Offline", tone: "idle" },
  disabled: { label: "Disabled", tone: "idle" },
};

const CAPABILITIES: [string, boolean, boolean][] = [
  ["Video capture, live preview, reconnection", true, true],
  ["Motion detection, motion objects, motion tracks", true, true],
  ["Line crossing & zones for motion objects", true, true],
  ["Local event storage, works offline", true, true],
  ["Person detection (YOLOX, on device)", false, true],
  ["Person tracking (ByteTrack, temporary IDs)", false, true],
  ["Person entry / exit / line crossing events", false, true],
  ["Visible clothing colours (on device)", false, true],
  ["Clothing type, bag, backpack, headwear (Claude, cloud, optional)", false, true],
];

function err(e: unknown) {
  return e instanceof ApiError ? e.message : "Edge agent is unreachable.";
}

export function AIView() {
  const { token, can } = usePermissions();
  const canManage = can("ai:manage");
  const statusFetcher = useCallback(
    (signal: AbortSignal) => (token ? agent.aiStatus(token, signal) : Promise.reject(new Error("Not signed in"))),
    [token],
  );
  const errorsFetcher = useCallback(
    (signal: AbortSignal) => (token ? agent.aiErrors(token, signal) : Promise.reject(new Error("Not signed in"))),
    [token],
  );
  const status = usePolling(statusFetcher, 2_000);
  const errors = usePolling(errorsFetcher, 5_000);
  const [config, setConfig] = useState<AIConfig | null>(null);
  const [models, setModels] = useState<AIModel[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const loadStatic = useCallback(async () => {
    if (!token) return;
    try {
      const [c, m] = await Promise.all([agent.aiConfig(token), agent.aiModels(token)]);
      setConfig(c);
      setModels(m);
    } catch (e) {
      setMessage({ ok: false, text: err(e) });
    }
  }, [token]);

  const mode = status.data?.mode;
  // Model status changes with the AI mode (loading → loaded, unloaded on failure): refresh the list then.
  useEffect(() => {
    void Promise.resolve().then(loadStatic);
  }, [loadStatic, mode]);

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage({ ok: true, text: ok });
      status.refresh();
      await loadStatic();
    } catch (e) {
      setMessage({ ok: false, text: err(e) });
    } finally {
      setBusy(null);
    }
  }

  const s = status.data;
  const meta = s ? MODE[s.mode] : null;
  const set = <K extends keyof AIConfig>(k: K, v: AIConfig[K]) => setConfig((c) => (c ? { ...c, [k]: v } : c));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="AI Engine"
        description="Optional person detection on the edge device. If AI is off or fails, cameras, motion detection and local storage keep working."
        actions={
          s &&
          canManage && (
            <label className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm">
              {busy === "toggle" ? <Loader2 className="size-4 animate-spin" /> : <BrainCircuit className="size-4" />}
              AI enabled
              <Switch
                aria-label="AI enabled"
                checked={s.enabled}
                disabled={busy !== null}
                onCheckedChange={(v) => token && act("toggle", () => (v ? agent.aiEnable(token) : agent.aiDisable(token)))}
              />
            </label>
          )
        }
      />

      {status.error && (
        <Alert variant="destructive">
          <AlertTitle>Edge agent unreachable</AlertTitle>
          <AlertDescription>{status.error.message}</AlertDescription>
        </Alert>
      )}
      {message && (
        <Alert variant={message.ok ? "default" : "destructive"}>
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}

      {s && meta && (
        <Panel title="Status" icon={BrainCircuit} actions={<StatusIndicator tone={meta.tone} label={meta.label} pulse={s.mode === "active"} />}>
          <p className="text-sm">{s.reason}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Since {formatDateTime(s.since)}
            {s.fallback && " · Processing continues on the Standard Engine (motion analysis without AI)."}
            {s.recovery.next_attempt_in_s != null && ` · Next recovery attempt in ${s.recovery.next_attempt_in_s}s (attempt ${s.recovery.attempts}).`}
          </p>
          {s.warnings.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-xs text-status-warning">
              {s.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <MetricTile label="Inference FPS" icon={Gauge} value={s.metrics.inference_fps} hint={`${s.metrics.inferences.toLocaleString()} inferences`} />
          <MetricTile
            label="Avg latency"
            icon={Timer}
            value={s.metrics.latency_avg_ms != null ? `${s.metrics.latency_avg_ms} ms` : null}
            hint={s.metrics.latency_p95_ms != null ? `p95 ${s.metrics.latency_p95_ms} ms` : "—"}
          />
          <MetricTile label="Detections" icon={Users} value={s.metrics.detections.toLocaleString()} hint={`${s.people_now} people now · ${s.people_seen_session} this session`} />
          <MetricTile label="Errors" icon={X} value={s.metrics.errors} hint={`${s.metrics.timeouts} timeouts`} />
          <MetricTile label="Agent memory" icon={MemoryStick} value={`${Math.round(s.resources.process_rss_mb)} MB`} hint={`limit ${s.resources.max_rss_mb} MB · free ${Math.round(s.resources.system_available_mb)} MB`} />
          <MetricTile
            label="GPU"
            icon={Cpu}
            value={s.resources.gpu.available ? `${s.resources.gpu.utilization_percent ?? 0}%` : "N/A"}
            hint={s.resources.gpu.available ? s.resources.gpu.name : (s.resources.gpu.detail ?? "Not available")}
          />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Model" icon={ShieldCheck} bodyClassName="p-0">
          <ul className="divide-y">
            {models.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {m.name}
                    {m.selected && <Badge variant="secondary" className="text-[10px]">selected</Badge>}
                  </div>
                  <div className="truncate font-mono text-[11px] text-muted-foreground">
                    {m.file} · {(m.size_bytes / 1_048_576).toFixed(1)} MB · v{m.version}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{m.detail || m.source}</div>
                </div>
                <Badge variant={m.status === "invalid" ? "destructive" : "outline"} className="font-mono text-[10px] uppercase">
                  {m.status}
                </Badge>
                {canManage && (
                  <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => token && act(`v-${m.id}`, () => agent.validateModel(token, m.id), `Checked ${m.name}`)}>
                    {busy === `v-${m.id}` ? <Loader2 className="animate-spin" /> : <Check />} Verify
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            Models are local files checked against a pinned SHA-256. The agent never downloads models by itself.
            {s?.model.backend && ` Running on ${s.model.backend.toUpperCase()} via ${s.model.provider}.`}
          </p>
        </Panel>

        <Panel title="Settings" icon={Gauge}>
          {config ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-1.5 sm:col-span-2">
                <Label htmlFor="ai-model">Model</Label>
                <Select value={config.model_id} onValueChange={(v) => set("model_id", v)} disabled={!canManage}>
                  <SelectTrigger id="ai-model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Field id="ai-conf" label="Confidence threshold" value={config.confidence_threshold} min={0.05} max={0.95} step={0.05} disabled={!canManage} onChange={(v) => set("confidence_threshold", v)} />
              <Field id="ai-fps" label="Analysis FPS per camera" value={config.process_fps} min={0.5} max={30} step={0.5} disabled={!canManage} onChange={(v) => set("process_fps", v)} />
              <div className="grid gap-1.5 sm:col-span-2">
                <Label htmlFor="ai-attrs">Appearance attributes</Label>
                <Select value={config.attributes} onValueChange={(v) => set("attributes", v as AttributeProviderKind)} disabled={!canManage}>
                  <SelectTrigger id="ai-attrs" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="off">Off</SelectItem>
                    <SelectItem value="local">On device — clothing colours only</SelectItem>
                    <SelectItem value="claude">On device + Claude (cloud) — type, bag, backpack, headwear</SelectItem>
                  </SelectContent>
                </Select>
                {config.attributes === "claude" && (
                  <p className="text-xs text-status-warning">
                    Sends one downscaled crop of each new person to Anthropic&apos;s API (rate-limited, not stored by the
                    agent). Requires internet and ACI_AGENT_AI_API_KEY. Only visible clothing and carried items are
                    described — never identity or personal traits. Make sure this fits your privacy policy.
                  </p>
                )}
              </div>
              {canManage && (
                <Button className="sm:col-span-2" disabled={busy !== null} onClick={() => token && act("save", () => agent.saveAiConfig(token, config), "AI settings saved")}>
                  {busy === "save" && <Loader2 className="animate-spin" />} Save AI settings
                </Button>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Standard vs AI Enhanced mode" icon={BrainCircuit} bodyClassName="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Capability</th>
                <th className="px-2 py-2 text-center font-medium">Standard</th>
                <th className="px-4 py-2 text-center font-medium">AI Enhanced</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {CAPABILITIES.map(([name, std, ai]) => (
                <tr key={name}>
                  <td className="px-4 py-1.5 text-xs">{name}</td>
                  <td className="px-2 py-1.5 text-center">{std ? <Check className="mx-auto size-4 text-status-online" /> : <X className="mx-auto size-4 text-muted-foreground/50" />}</td>
                  <td className="px-4 py-1.5 text-center">{ai ? <Check className="mx-auto size-4 text-status-online" /> : <X className="mx-auto size-4 text-muted-foreground/50" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            No face recognition, no biometric matching, no identity across cameras. Track IDs are temporary and per camera.
          </p>
        </Panel>

        <Panel title="Errors & status history" icon={X} bodyClassName="p-0">
          <ul className="max-h-80 divide-y overflow-y-auto">
            {(errors.data?.errors ?? []).map((e, i) => (
              <li key={`e${i}`} className="px-4 py-2 text-xs">
                <span className="font-mono text-muted-foreground">{formatDateTime(e.at)}</span>{" "}
                <Badge variant="destructive" className="mx-1 text-[10px]">{e.kind}</Badge>
                {e.detail}
              </li>
            ))}
            {(errors.data?.history ?? []).map((h, i) => (
              <li key={`h${i}`} className="px-4 py-2 text-xs">
                <span className="font-mono text-muted-foreground">{formatDateTime(h.at)}</span>{" "}
                <Badge variant="outline" className="mx-1 text-[10px] uppercase">{h.mode}</Badge>
                {h.reason}
              </li>
            ))}
            {!errors.data?.errors.length && !errors.data?.history.length && (
              <li className="px-4 py-4 text-center text-xs text-muted-foreground">No AI events yet.</li>
            )}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function Field(props: { id: string; label: string; value: number; min: number; max: number; step: number; disabled?: boolean; onChange: (v: number) => void }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={props.id}>{props.label}</Label>
      <Input
        id={props.id}
        type="number"
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step}
        disabled={props.disabled}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) props.onChange(Math.min(props.max, Math.max(props.min, v)));
        }}
      />
    </div>
  );
}
