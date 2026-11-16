"use client";

import {
  ArrowDown,
  ArrowDownLeft,
  ArrowDownRight,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ArrowUpLeft,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Clock,
  EyeOff,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import {
  platform,
  type AttributeValue,
  type Observation,
  type RegistryCamera,
} from "@/lib/api/platform";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";

const ALL = "__all__";
const PAGE = 24;
const PERIODS: Record<string, number> = {
  "1h": 1,
  "24h": 24,
  "7d": 168,
  "30d": 720,
};
const ATTRIBUTE_LABELS: Record<string, string> = {
  upper_color: "Top colour",
  lower_color: "Bottom colour",
  upper_type: "Top",
  lower_type: "Bottom",
  bag: "Bag",
  backpack: "Backpack",
  headwear: "Headwear",
};

function attributes(o: Observation): {
  label: string;
  value: string;
  confidence: number | null;
  source?: string;
}[] {
  if (!o.attributes) return [];
  return Object.entries(o.attributes)
    .filter(
      ([k, v]) =>
        k in ATTRIBUTE_LABELS &&
        v &&
        typeof v === "object" &&
        (v as AttributeValue).value !== "unknown",
    )
    .map(([k, v]) => {
      const a = v as AttributeValue;
      return {
        label: ATTRIBUTE_LABELS[k],
        value: a.value,
        confidence: a.confidence ?? null,
        source: a.source,
      };
    });
}

/** Heading reported by the tracker: compass points in image space (N = up) or left/right words. */
const HEADING: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; label: string }
> = {
  N: { icon: ArrowUp, label: "up (N)" },
  S: { icon: ArrowDown, label: "down (S)" },
  E: { icon: ArrowRight, label: "right (E)" },
  W: { icon: ArrowLeft, label: "left (W)" },
  NE: { icon: ArrowUpRight, label: "up-right (NE)" },
  NW: { icon: ArrowUpLeft, label: "up-left (NW)" },
  SE: { icon: ArrowDownRight, label: "down-right (SE)" },
  SW: { icon: ArrowDownLeft, label: "down-left (SW)" },
};

function Direction({ value }: { value: string | null }) {
  if (!value) return <span className="text-muted-foreground">—</span>;
  const h = HEADING[value.toUpperCase()];
  const Icon =
    h?.icon ??
    (value.includes("left")
      ? ArrowLeft
      : value.includes("right")
        ? ArrowRight
        : null);
  return (
    <span className="inline-flex items-center gap-1">
      {Icon && <Icon className="size-3.5" aria-hidden />} {h?.label ?? value}
    </span>
  );
}

function ObservationCard({ o }: { o: Observation }) {
  const attrs = attributes(o);
  return (
    <article
      data-testid="observation"
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 text-sm"
    >
      <header className="flex items-center gap-2">
        <span className="grid size-7 place-items-center rounded-full bg-secondary">
          <UserRound className="size-3.5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-xs">Track #{o.track_id}</div>
          <div className="truncate text-xs text-muted-foreground">
            {o.camera_name ?? o.camera_id} · {o.device_name ?? "device"}
          </div>
        </div>
        <Badge variant={o.ended ? "outline" : "default"}>
          {o.ended ? "ended" : "in view"}
        </Badge>
      </header>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Seen</dt>
        <dd className="text-right font-mono">{formatDateTime(o.first_seen)}</dd>
        <dt className="text-muted-foreground">Duration</dt>
        <dd className="text-right font-mono">{o.duration_s.toFixed(1)} s</dd>
        <dt className="text-muted-foreground">Direction</dt>
        <dd className="text-right">
          <Direction value={o.direction} />
        </dd>
        <dt className="text-muted-foreground">Confidence</dt>
        <dd className="text-right font-mono">
          {o.confidence != null ? `${Math.round(o.confidence * 100)}%` : "—"}
        </dd>
        <dt className="text-muted-foreground">Source</dt>
        <dd className="truncate text-right font-mono" title={o.detector ?? ""}>
          AI · {o.detector ?? "detector"}
        </dd>
        {o.lines_crossed.length > 0 && (
          <>
            <dt className="text-muted-foreground">Lines</dt>
            <dd className="truncate text-right font-mono">
              {o.lines_crossed.length}
            </dd>
          </>
        )}
      </dl>
      {attrs.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label="Clothing attributes">
          {attrs.map((a) => (
            <li key={a.label}>
              <Badge
                variant="secondary"
                className="font-normal"
                title={a.source ? `source: ${a.source}` : undefined}
              >
                {a.label}: {a.value}
                {a.confidence != null && (
                  <span className="ml-1 text-muted-foreground">
                    {Math.round(a.confidence * 100)}%
                  </span>
                )}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      {o.attributes_hidden && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <EyeOff className="size-3" /> Attributes hidden by privacy settings
        </p>
      )}
    </article>
  );
}

export function ObservationsView() {
  const { token, can } = usePermissions();
  const allowed = can("observations:read");
  const [period, setPeriod] = useState("24h");
  const [camera, setCamera] = useState(ALL);
  const [offset, setOffset] = useState(0);
  const [cameras, setCameras] = useState<RegistryCamera[]>([]);
  const [data, setData] = useState<{
    items: Observation[];
    total: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (token && can("cameras:read"))
      platform.cameras(token).then(setCameras, () => setCameras([]));
  }, [token, can]);

  const load = useCallback(
    async (signal: AbortSignal) => {
      if (!token || !allowed) return;
      setLoading(true);
      try {
        const [deviceId, cameraRef] =
          camera === ALL ? [undefined, undefined] : camera.split("|");
        const since = new Date(
          Date.now() - PERIODS[period] * 3_600_000,
        ).toISOString();
        const r = await platform.observations(
          token,
          {
            since,
            device_id: deviceId,
            camera_ref: cameraRef,
            offset,
            limit: PAGE,
          },
          signal,
        );
        setData(r);
        setError(null);
      } catch (e) {
        if (!signal.aborted)
          setError(
            e instanceof ApiError
              ? e.message
              : "Central server is unreachable.",
          );
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [token, allowed, camera, period, offset],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    return () => controller.abort();
  }, [load, reload]);

  // New person events → refresh the first page (debounced by the server batching).
  useLiveMessages(["event.created"], (m) => {
    const events =
      (m.data.events as { event_type?: string }[] | undefined) ?? [];
    if (offset === 0 && events.some((e) => e.event_type?.startsWith("person_")))
      setReload((n) => n + 1);
  });

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Anonymous observations"
        description="What the AI engine saw: one card per temporary track on one camera."
      />
      <Alert>
        <ShieldCheck className="size-4" />
        <AlertTitle>No identification</AlertTitle>
        <AlertDescription>
          No faces are analysed or stored. Track IDs are temporary and belong to
          one camera; observations are never linked across cameras or over time
          into a profile of a person.
        </AlertDescription>
      </Alert>

      {!allowed ? (
        <Alert variant="destructive">
          <AlertTitle>No access</AlertTitle>
          <AlertDescription>
            Your role doesn&apos;t include the observations:read permission.
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-2">
            <Select
              value={period}
              onValueChange={(v) => (setPeriod(v), setOffset(0))}
            >
              <SelectTrigger className="w-40" aria-label="Period">
                <Clock className="size-4" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1h">Last hour</SelectItem>
                <SelectItem value="24h">Last 24 hours</SelectItem>
                <SelectItem value="7d">Last 7 days</SelectItem>
                <SelectItem value="30d">Last 30 days</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={camera}
              onValueChange={(v) => (setCamera(v), setOffset(0))}
            >
              <SelectTrigger className="w-60" aria-label="Camera filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All cameras</SelectItem>
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
            <span className="ml-auto text-xs text-muted-foreground">
              {data ? `${data.total} observations` : ""}
            </span>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertTitle>Observations unavailable</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div
            className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
            data-testid="observations"
          >
            {loading && !data
              ? Array.from({ length: 6 }, (_, i) => (
                  <Skeleton key={i} className="h-44" />
                ))
              : data?.items.map((o) => <ObservationCard key={o.id} o={o} />)}
          </div>
          {data && data.items.length === 0 && (
            <p className="rounded-lg border p-6 text-sm text-muted-foreground">
              No person observations in this period. They appear when the AI
              engine is enabled on a device and detects people.
            </p>
          )}
          {data && data.total > PAGE && (
            <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
              <Button
                size="sm"
                variant="ghost"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
              >
                <ChevronLeft /> Newer
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={offset + PAGE >= data.total}
                onClick={() => setOffset((o) => o + PAGE)}
              >
                Older <ChevronRight />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
