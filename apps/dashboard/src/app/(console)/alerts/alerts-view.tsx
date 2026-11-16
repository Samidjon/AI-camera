"use client";

import {
  AlertOctagon,
  AlertTriangle,
  BellRing,
  Check,
  CheckCheck,
  Info,
  Mail,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api/client";
import {
  insights,
  SEVERITY_LABEL,
  type AlertCatalog,
  type AlertItem,
  type AlertRule,
  type AlertRuleInput,
  type Delivery,
} from "@/lib/api/insights";
import { platform, type RegistryCamera } from "@/lib/api/platform";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";
import { cn } from "@/lib/utils";

const ANY = "__any__";

/** Status colour never carries meaning alone: icon + label always accompany it. */
export function SeverityBadge({
  severity,
}: {
  severity: AlertItem["severity"];
}) {
  const Icon =
    severity === "critical"
      ? AlertOctagon
      : severity === "warning"
        ? AlertTriangle
        : Info;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        severity === "critical" &&
          "border-[var(--status-error)] text-[var(--status-error)]",
        severity === "warning" &&
          "border-[var(--status-warning)] text-[var(--status-warning)]",
        severity === "info" && "text-muted-foreground",
      )}
    >
      <Icon className="size-3" aria-hidden /> {SEVERITY_LABEL[severity]}
    </span>
  );
}

function errorText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.message : fallback;
}

// ---------------------------------------------------------------------------- alerts list

function AlertsList() {
  const { token, can } = usePermissions();
  const canAck = can("events:ack");
  const [filter, setFilter] = useState("active");
  const [items, setItems] = useState<AlertItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Record<string, Delivery[]>>({});

  useEffect(() => {
    if (!token) return;
    const c = new AbortController();
    insights
      .alerts(token, filter === "all" ? undefined : filter, c.signal)
      .then(
        (r) => (setItems(r), setError(null)),
        (e) =>
          !c.signal.aborted &&
          setError(errorText(e, "Central server is unreachable.")),
      );
    return () => c.abort();
  }, [token, filter, reload]);

  useLiveMessages(["alert.created"], () => setReload((n) => n + 1));

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      setReload((n) => n + 1);
      window.dispatchEvent(new Event("aci:alerts-changed"));
    } catch (e) {
      setError(errorText(e, "Action failed."));
    }
  }

  async function toggle(id: string) {
    setOpen((o) => (o === id ? null : id));
    if (token && !deliveries[id])
      insights.deliveries(token, id).then(
        (d) => setDeliveries((m) => ({ ...m, [id]: d })),
        () => {},
      );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-2">
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="w-44" aria-label="Alert status filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active (open + acknowledged)</SelectItem>
            <SelectItem value="open">Open</SelectItem>
            <SelectItem value="acknowledged">Acknowledged</SelectItem>
            <SelectItem value="resolved">Resolved</SelectItem>
            <SelectItem value="all">All</SelectItem>
          </SelectContent>
        </Select>
        {canAck && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={!items?.some((a) => a.status === "open")}
            onClick={() => token && act(() => insights.acknowledgeAll(token))}
          >
            <CheckCheck /> Acknowledge all
          </Button>
        )}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Alerts unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {items && items.length === 0 && (
        <p className="rounded-lg border p-6 text-sm text-muted-foreground">
          No alerts. Alerts are raised only by the rules on the “Rules” tab.
        </p>
      )}
      <ul className="flex flex-col gap-2" data-testid="alerts">
        {items?.map((a) => (
          <li
            key={a.id}
            className={cn(
              "rounded-lg border bg-card p-3 text-sm",
              a.status === "resolved" && "opacity-70",
            )}
          >
            <div className="flex flex-wrap items-start gap-3">
              <SeverityBadge severity={a.severity} />
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => toggle(a.id)}
              >
                <div className="font-medium">{a.title}</div>
                <div className="text-xs text-muted-foreground">{a.message}</div>
              </button>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {formatDateTime(a.created_at)}
                </span>
                <Badge variant={a.status === "open" ? "default" : "outline"}>
                  {a.status}
                </Badge>
                {canAck && a.status === "open" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      token && act(() => insights.acknowledge(token, a.id))
                    }
                  >
                    <Check /> Acknowledge
                  </Button>
                )}
                {canAck && a.status !== "resolved" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      token && act(() => insights.resolve(token, a.id))
                    }
                  >
                    Resolve
                  </Button>
                )}
              </div>
            </div>
            {open === a.id && (
              <div className="mt-2 border-t pt-2 text-xs text-muted-foreground">
                {a.context.rule_name != null && (
                  <div>Rule: {String(a.context.rule_name)}</div>
                )}
                {a.acknowledged_at && (
                  <div>Acknowledged {formatDateTime(a.acknowledged_at)}</div>
                )}
                {a.resolved_at && (
                  <div>Resolved {formatDateTime(a.resolved_at)}</div>
                )}
                <div className="mt-1">
                  Notifications:{" "}
                  {(deliveries[a.id] ?? []).map((d, i) => (
                    <span key={i} className="mr-2">
                      {d.channel === "email"
                        ? `e-mail ${d.recipient ?? ""}`
                        : "in-app"}
                      : {d.status}
                      {d.error ? ` (${d.error})` : ""}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------- rules

const EMPTY: AlertRuleInput = {
  name: "",
  kind: "camera_offline",
  enabled: true,
  severity: null,
  threshold: null,
  device_id: null,
  camera_ref: null,
  zone_id: null,
  line_id: null,
  cooldown_s: 300,
  channels: ["in_app"],
  email_recipients: [],
};

function RuleDialog({
  rule,
  catalog,
  cameras,
  onClose,
  onSaved,
}: {
  rule: AlertRule | "new";
  catalog: AlertCatalog;
  cameras: RegistryCamera[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { token } = usePermissions();
  const [form, setForm] = useState<AlertRuleInput>(() =>
    rule === "new" ? EMPTY : { ...rule },
  );
  const [recipients, setRecipients] = useState(() =>
    rule === "new" ? "" : rule.email_recipients.join(", "),
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const info = catalog.kinds.find((k) => k.kind === form.kind);
  const set = <K extends keyof AlertRuleInput>(k: K, v: AlertRuleInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }));
  const scope =
    form.device_id && form.camera_ref
      ? `${form.device_id}|${form.camera_ref}`
      : ANY;
  const groups = [...new Set(catalog.kinds.map((k) => k.group))];

  async function save() {
    if (!token) return;
    setSaving(true);
    const body: AlertRuleInput = {
      ...form,
      name: form.name.trim() || info?.label || form.kind,
      email_recipients: recipients
        .split(/[,;\s]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    };
    try {
      if (rule === "new") await insights.createRule(token, body);
      else await insights.updateRule(token, rule.id, body);
      onSaved();
    } catch (e) {
      setError(errorText(e, "Saving failed."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {rule === "new" ? "New alert rule" : "Edit alert rule"}
          </DialogTitle>
          <DialogDescription>
            An alert is raised when the condition happens, at most once per
            cooldown for each camera/device.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="rule-kind">Condition</Label>
            <Select value={form.kind} onValueChange={(v) => set("kind", v)}>
              <SelectTrigger id="rule-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {groups.map((g) =>
                  catalog.kinds
                    .filter((k) => k.group === g)
                    .map((k) => (
                      <SelectItem key={k.kind} value={k.kind}>
                        {g} · {k.label}
                      </SelectItem>
                    )),
                )}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="rule-name">Name</Label>
            <Input
              id="rule-name"
              value={form.name}
              placeholder={info?.label}
              onChange={(e) => set("name", e.target.value)}
            />
          </div>
          {info?.metric && (
            <div className="grid gap-1">
              <Label htmlFor="rule-threshold">
                Threshold ({info.unit}) — default {info.threshold}
              </Label>
              <Input
                id="rule-threshold"
                type="number"
                value={form.threshold ?? ""}
                placeholder={String(info.threshold ?? "")}
                onChange={(e) =>
                  set(
                    "threshold",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              />
            </div>
          )}
          {!info?.metric &&
            form.kind !== "device_offline" &&
            !form.kind.startsWith("ai_") &&
            form.kind !== "model_error" && (
              <div className="grid gap-1">
                <Label>Camera</Label>
                <Select
                  value={scope}
                  onValueChange={(v) => {
                    const [d, c] = v === ANY ? [null, null] : v.split("|");
                    setForm((f) => ({ ...f, device_id: d, camera_ref: c }));
                  }}
                >
                  <SelectTrigger aria-label="Camera scope">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>Any camera</SelectItem>
                    {cameras.map((c) => (
                      <SelectItem
                        key={c.id}
                        value={`${c.device_id}|${c.external_id}`}
                      >
                        {c.name} · {c.device_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1">
              <Label>Severity</Label>
              <Select
                value={form.severity ?? "default"}
                onValueChange={(v) =>
                  set(
                    "severity",
                    v === "default" ? null : (v as AlertRule["severity"]),
                  )
                }
              >
                <SelectTrigger aria-label="Severity">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">
                    Default ({info?.severity})
                  </SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                  <SelectItem value="warning">Warning</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="rule-cooldown">Cooldown (minutes)</Label>
              <Input
                id="rule-cooldown"
                type="number"
                min={0}
                value={Math.round(form.cooldown_s / 60)}
                onChange={(e) =>
                  set(
                    "cooldown_s",
                    Math.max(0, Math.min(1440, Number(e.target.value) || 0)) *
                      60,
                  )
                }
              />
            </div>
          </div>
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <Label htmlFor="rule-email">Also send e-mail</Label>
            <Switch
              id="rule-email"
              checked={form.channels.includes("email")}
              onCheckedChange={(on) =>
                set("channels", on ? ["in_app", "email"] : ["in_app"])
              }
            />
          </div>
          {form.channels.includes("email") && (
            <div className="grid gap-1">
              <Label htmlFor="rule-recipients">
                Recipients (comma-separated)
              </Label>
              <Input
                id="rule-recipients"
                value={recipients}
                onChange={(e) => setRecipients(e.target.value)}
                placeholder="ops@example.com"
              />
              {!catalog.email.configured && (
                <p className="text-xs text-[var(--status-warning)]">
                  SMTP is not configured on the server — e-mails will be
                  recorded as skipped until it is.
                </p>
              )}
            </div>
          )}
          <div className="flex items-center justify-between">
            <Label htmlFor="rule-enabled">Enabled</Label>
            <Switch
              id="rule-enabled"
              checked={form.enabled}
              onCheckedChange={(v) => set("enabled", v)}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            Save rule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RulesPanel() {
  const { token, can } = usePermissions();
  const manage = can("settings:manage");
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [catalog, setCatalog] = useState<AlertCatalog | null>(null);
  const [cameras, setCameras] = useState<RegistryCamera[]>([]);
  const [editing, setEditing] = useState<AlertRule | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [testTo, setTestTo] = useState("");
  const [testMsg, setTestMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    insights
      .catalog(token)
      .then(setCatalog, (e) => setError(errorText(e, "Unavailable")));
    if (can("cameras:read")) platform.cameras(token).then(setCameras, () => {});
  }, [token, can]);

  useEffect(() => {
    if (!token) return;
    insights
      .rules(token)
      .then(setRules, (e) => setError(errorText(e, "Unavailable")));
  }, [token, reload]);

  const label = useMemo(
    () =>
      Object.fromEntries((catalog?.kinds ?? []).map((k) => [k.kind, k.label])),
    [catalog],
  );
  const CAMERA_KINDS = [
    "camera_offline",
    "camera_reconnected",
    "stream_error",
    "motion_detected",
    "zone_activity",
    "line_crossed",
  ];
  const camName = (r: AlertRule) =>
    !CAMERA_KINDS.includes(r.kind)
      ? "whole device"
      : r.camera_ref
        ? (cameras.find(
            (c) =>
              c.external_id === r.camera_ref && c.device_id === r.device_id,
          )?.name ?? r.camera_ref)
        : "any camera";

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setReload((n) => n + 1);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Action failed."));
    }
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {manage && (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            onClick={() => setEditing("new")}
            disabled={!catalog}
          >
            <Plus /> New rule
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => token && act(() => insights.addRecommended(token))}
          >
            <Sparkles /> Add recommended health rules
          </Button>
        </div>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rules && rules.length === 0 && (
        <p className="rounded-lg border p-6 text-sm text-muted-foreground">
          No rules yet — nothing will raise alerts. Start with the recommended
          health rules (camera offline, AI offline, high CPU …).
        </p>
      )}
      <ul className="flex flex-col gap-2" data-testid="alert-rules">
        {rules?.map((r) => (
          <li
            key={r.id}
            className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-3 py-2 text-sm"
          >
            <SeverityBadge severity={r.severity} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{r.name}</div>
              <div className="text-xs text-muted-foreground">
                {label[r.kind] ?? r.kind}
                {r.threshold != null && ` · threshold ${r.threshold}`} ·{" "}
                {camName(r)} · cooldown {Math.round(r.cooldown_s / 60)} min
                {r.channels.includes("email") && (
                  <span className="ml-1 inline-flex items-center gap-0.5">
                    · <Mail className="size-3" /> {r.email_recipients.length}
                  </span>
                )}
              </div>
            </div>
            {manage ? (
              <>
                <Switch
                  aria-label={`Enable ${r.name}`}
                  checked={r.enabled}
                  onCheckedChange={(enabled) =>
                    token &&
                    act(() =>
                      insights.updateRule(token, r.id, { ...r, enabled }),
                    )
                  }
                />
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Edit ${r.name}`}
                  onClick={() => setEditing(r)}
                >
                  <Pencil />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Delete ${r.name}`}
                  onClick={() =>
                    token && act(() => insights.deleteRule(token, r.id))
                  }
                >
                  <Trash2 />
                </Button>
              </>
            ) : (
              <Badge variant="outline">
                {r.enabled ? "enabled" : "disabled"}
              </Badge>
            )}
          </li>
        ))}
      </ul>

      {manage && catalog && (
        <Panel title="E-mail notifications" icon={Mail}>
          <p className="mb-2 text-xs text-muted-foreground">
            {catalog.email.configured
              ? `SMTP configured — sending from ${catalog.email.from ?? "the configured address"}.`
              : "SMTP is not configured. Set ACI_SMTP_HOST, ACI_SMTP_PORT, ACI_SMTP_USERNAME, ACI_SMTP_PASSWORD and ACI_SMTP_FROM in the server .env, then restart the server."}
          </p>
          <div className="flex flex-wrap gap-2">
            <Input
              className="w-64"
              type="email"
              placeholder="you@example.com"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              aria-label="Test e-mail recipient"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!testTo || !catalog.email.configured}
              onClick={async () => {
                if (!token) return;
                try {
                  await insights.testEmail(token, testTo);
                  setTestMsg("Test e-mail sent.");
                } catch (e) {
                  setTestMsg(errorText(e, "Sending failed."));
                }
              }}
            >
              Send test e-mail
            </Button>
          </div>
          {testMsg && <p className="mt-2 text-xs">{testMsg}</p>}
        </Panel>
      )}

      {editing && catalog && (
        <RuleDialog
          rule={editing}
          catalog={catalog}
          cameras={cameras}
          onClose={() => setEditing(null)}
          onSaved={() => (setEditing(null), setReload((n) => n + 1))}
        />
      )}
    </div>
  );
}

export function AlertsView() {
  const { can } = usePermissions();
  if (!can("events:read"))
    return (
      <div className="flex flex-col gap-4">
        <PageHeader
          title="Alerts"
          description="Notifications raised by your alert rules"
        />
        <Alert variant="destructive">
          <AlertTitle>No access</AlertTitle>
          <AlertDescription>
            Your role doesn&apos;t include the events:read permission.
          </AlertDescription>
        </Alert>
      </div>
    );
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Alerts"
        description="Notifications raised by your alert rules — in the app and by e-mail."
      />
      <Tabs defaultValue="alerts">
        <TabsList>
          <TabsTrigger value="alerts">
            <BellRing /> Alerts
          </TabsTrigger>
          <TabsTrigger value="rules">Rules</TabsTrigger>
        </TabsList>
        <TabsContent value="alerts" className="mt-3">
          <AlertsList />
        </TabsContent>
        <TabsContent value="rules" className="mt-3">
          <RulesPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
