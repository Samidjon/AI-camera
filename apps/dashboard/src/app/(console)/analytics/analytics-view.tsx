"use client";

import {
  Activity,
  ArrowDownToLine,
  BrainCircuit,
  Clock,
  FileDown,
  Info,
  LogIn,
  LogOut,
  RefreshCw,
  Timer,
  UsersRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  BarList,
  ChartFrame,
  ColumnChart,
  DataTable,
  Heatmap,
  type Point,
} from "@/components/charts/charts";
import { MetricTile } from "@/components/common/metric-tile";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
  insights,
  saveBlob,
  type AnalyticsSummary,
  type ReportKind,
} from "@/lib/api/insights";
import { platform, type RegistryCamera } from "@/lib/api/platform";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";

const ALL = "__all__";
const PERIODS: Record<string, { hours: number; label: string }> = {
  "24h": { hours: 24, label: "Last 24 hours" },
  "7d": { hours: 168, label: "Last 7 days" },
  "30d": { hours: 720, label: "Last 30 days" },
  "90d": { hours: 2160, label: "Last 90 days" },
};
const REPORTS: {
  kind: ReportKind;
  label: string;
  needs?: "device" | "camera";
}[] = [
  { kind: "daily", label: "Daily" },
  { kind: "weekly", label: "Weekly" },
  { kind: "monthly", label: "Monthly" },
  { kind: "zone", label: "Zones & lines" },
  { kind: "ai", label: "AI performance" },
  { kind: "device", label: "Device", needs: "device" },
  { kind: "camera", label: "Camera", needs: "camera" },
];

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)} s`;
  if (s < 3600) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

function hourLabel(bucket: string, daily: boolean): Point {
  // bucket: "YYYY-MM-DD HH:00" (local to the chosen zone) or "YYYY-MM-DD"
  const [d, t] = bucket.split(" ");
  return {
    label: daily ? d.slice(5) : (t ?? d),
    title: bucket,
    value: 0,
  };
}

function series(
  buckets: { bucket: string; value: number }[],
  daily: boolean,
): Point[] {
  return buckets.map((b) => ({
    ...hourLabel(b.bucket, daily),
    value: b.value,
  }));
}

export function AnalyticsView() {
  const { token, can } = usePermissions();
  const tz = useMemo(() => browserTz(), []);
  const [period, setPeriod] = useState("24h");
  const [camera, setCamera] = useState(ALL);
  const [cameras, setCameras] = useState<RegistryCamera[]>([]);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (token && can("cameras:read"))
      platform.cameras(token).then(setCameras, () => setCameras([]));
  }, [token, can]);

  const query = useMemo(() => {
    const [device_id, camera_ref] =
      camera === ALL ? [undefined, undefined] : camera.split("|");
    return { device_id, camera_ref, tz };
  }, [camera, tz]);

  const load = useCallback(
    async (signal: AbortSignal) => {
      if (!token || !can("events:read")) return;
      setLoading(true);
      try {
        const until = new Date();
        const since = new Date(
          until.getTime() - PERIODS[period].hours * 3_600_000,
        );
        const r = await insights.summary(
          token,
          { ...query, since: since.toISOString(), until: until.toISOString() },
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
    [token, can, period, query],
  );

  useEffect(() => {
    const c = new AbortController();
    void Promise.resolve().then(() => load(c.signal));
    return () => c.abort();
  }, [load, reload]);

  async function downloadReport(kind: ReportKind, format: "csv" | "pdf") {
    if (!token) return;
    setBusy(`${kind}.${format}`);
    try {
      const custom =
        kind === "device" ||
        kind === "camera" ||
        kind === "zone" ||
        kind === "ai";
      const until = new Date();
      const since = new Date(
        until.getTime() - PERIODS[period].hours * 3_600_000,
      );
      saveBlob(
        await insights.report(token, kind, format, {
          ...query,
          ...(custom
            ? { since: since.toISOString(), until: until.toISOString() }
            : {}),
        }),
      );
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Report download failed.");
    } finally {
      setBusy(null);
    }
  }

  if (!can("events:read"))
    return (
      <div className="flex flex-col gap-4">
        <PageHeader
          title="Analytics"
          description="Activity statistics built from real events"
        />
        <Alert variant="destructive">
          <AlertTitle>No access</AlertTitle>
          <AlertDescription>
            Your role doesn&apos;t include the events:read permission.
          </AlertDescription>
        </Alert>
      </div>
    );

  const std = data?.standard;
  const ai = data?.ai;
  const daily = PERIODS[period].hours > 72;
  const selected = cameras.find(
    (c) => `${c.device_id}|${c.external_id}` === camera,
  );

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Analytics"
        description="Computed on the server from stored events only. Motion analytics never count people."
      />

      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-2">
        <Select value={period} onValueChange={setPeriod}>
          <SelectTrigger className="w-44" aria-label="Period">
            <Clock className="size-4" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(PERIODS).map(([k, v]) => (
              <SelectItem key={k} value={k}>
                {v.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={camera} onValueChange={setCamera}>
          <SelectTrigger className="w-60" aria-label="Camera filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All cameras</SelectItem>
            {cameras.map((c) => (
              <SelectItem key={c.id} value={`${c.device_id}|${c.external_id}`}>
                {c.name} · {c.device_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setReload((n) => n + 1)}
          disabled={loading}
        >
          <RefreshCw className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
        <span className="ml-auto text-xs text-muted-foreground">
          Time zone {tz}
          {data && ` · updated ${formatDateTime(data.generated_at)}`}
        </span>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Analytics unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* ------------------------------------------------------------ standard */}
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Activity className="size-4" /> Standard analytics{" "}
        <span className="font-normal text-muted-foreground">
          (motion — works without AI)
        </span>
      </h2>
      <div
        className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        data-testid="standard-kpis"
      >
        <MetricTile
          label="Motion events"
          value={std ? std.motion_events.toLocaleString() : null}
          icon={Activity}
        />
        <MetricTile
          label="Activity time"
          value={std ? duration(std.activity_duration_s) : null}
          icon={Timer}
        />
        <MetricTile
          label="Frequency"
          value={std ? `${std.motion_frequency_per_hour}/h` : null}
          hint="motion events per hour"
        />
        <MetricTile
          label="Zone entries"
          value={
            std
              ? std.zones.reduce((a, z) => a + z.entries, 0).toLocaleString()
              : null
          }
          hint="moving objects, not people"
        />
      </div>

      {loading && !data ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Skeleton className="h-60" />
          <Skeleton className="h-60" />
        </div>
      ) : (
        std && (
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title={daily ? "Motion per day" : "Motion per hour"}>
              <ChartFrame
                empty={
                  std.motion_events === 0
                    ? "No motion events in this period."
                    : null
                }
                table={
                  <DataTable
                    columns={[daily ? "Day" : "Hour", "Motion events"]}
                    rows={(daily ? std.daily : std.hourly).map((b) => [
                      b.bucket,
                      b.value,
                    ])}
                  />
                }
              >
                <ColumnChart
                  ariaLabel="Motion events over time"
                  data={series(daily ? std.daily : std.hourly, daily)}
                  unit="events"
                />
              </ChartFrame>
            </Panel>
            <Panel title="Activity by weekday and hour">
              <ChartFrame
                empty={
                  std.motion_events === 0
                    ? "No motion events in this period."
                    : null
                }
                table={
                  <DataTable
                    columns={["Weekday", "Hour", "Motion events"]}
                    rows={std.heatmap
                      .filter((c) => c.value)
                      .map((c) => [
                        ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][
                          c.weekday
                        ],
                        c.hour,
                        c.value,
                      ])}
                  />
                }
              >
                <Heatmap
                  cells={std.heatmap}
                  ariaLabel="Motion heatmap by weekday and hour"
                />
              </ChartFrame>
            </Panel>
            <Panel title="Camera comparison">
              <ChartFrame
                empty={std.cameras.length === 0 ? "No camera activity." : null}
                table={
                  <DataTable
                    columns={["Camera", "Motion events", "Activity (s)"]}
                    rows={std.cameras.map((c) => [
                      c.camera_name ?? c.camera_ref ?? "—",
                      c.motion_events,
                      c.activity_duration_s,
                    ])}
                  />
                }
              >
                <BarList
                  rows={std.cameras
                    .slice(0, 12)
                    .map((c) => ({
                      label: c.camera_name ?? c.camera_ref ?? "—",
                      value: c.motion_events,
                    }))}
                />
              </ChartFrame>
            </Panel>
            <Panel title="Zones & lines">
              {std.zones.length === 0 && std.lines.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No zone or line events. Draw zones and lines on a camera to
                  see them here.
                </p>
              ) : (
                <DataTable
                  columns={[
                    "Zone / line",
                    "Camera",
                    "In / entries",
                    "Out / exits",
                    "Estimate / avg dwell",
                  ]}
                  rows={[
                    ...std.zones.map((z) => [
                      `Zone · ${z.name}`,
                      z.camera_name ?? z.camera_ref ?? "—",
                      z.entries,
                      z.exits,
                      `${z.occupancy_estimate} now · ${duration(z.avg_dwell_s)}`,
                    ]),
                    ...std.lines.map((l) => [
                      `Line · ${l.name}`,
                      l.camera_name ?? l.camera_ref ?? "—",
                      l.forward,
                      l.backward,
                      "—",
                    ]),
                  ]}
                />
              )}
            </Panel>
          </div>
        )
      )}

      {/* ------------------------------------------------------------ AI */}
      <h2 className="mt-2 flex items-center gap-2 text-sm font-semibold">
        <BrainCircuit className="size-4" /> AI analytics{" "}
        <span className="font-normal text-muted-foreground">
          (people — only when the AI engine detected people)
        </span>
      </h2>
      {ai && !ai.available ? (
        <Alert data-testid="ai-unavailable">
          <Info className="size-4" />
          <AlertTitle>No people analytics</AlertTitle>
          <AlertDescription>{ai.reason}</AlertDescription>
        </Alert>
      ) : (
        ai?.available && (
          <>
            <div
              className="grid grid-cols-2 gap-3 lg:grid-cols-4"
              data-testid="ai-kpis"
            >
              <MetricTile
                label="People observations"
                value={ai.people_observations.toLocaleString()}
                icon={UsersRound}
                hint="one per temporary track"
              />
              <MetricTile
                label="Inbound"
                value={ai.inbound.toLocaleString()}
                icon={LogIn}
                hint="line crossed forward"
              />
              <MetricTile
                label="Outbound"
                value={ai.outbound.toLocaleString()}
                icon={LogOut}
                hint="line crossed backward"
              />
              <MetricTile
                label="Avg. time in view"
                value={duration(ai.avg_observation_duration_s)}
                icon={Timer}
              />
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              <Panel title={daily ? "People per day" : "People per hour"}>
                <ChartFrame
                  table={
                    <DataTable
                      columns={[daily ? "Day" : "Hour", "Observations"]}
                      rows={(daily ? ai.daily : ai.hourly).map((b) => [
                        b.bucket,
                        b.value,
                      ])}
                    />
                  }
                >
                  <ColumnChart
                    ariaLabel="People observations over time"
                    data={series(daily ? ai.daily : ai.hourly, daily)}
                    unit="people"
                  />
                </ChartFrame>
              </Panel>
              <Panel title="Time-of-day distribution">
                <ChartFrame
                  table={
                    <DataTable
                      columns={["Hour", "Observations"]}
                      rows={ai.time_distribution.map((b) => [
                        `${b.hour}:00`,
                        b.value,
                      ])}
                    />
                  }
                >
                  <ColumnChart
                    ariaLabel="People observations by hour of day"
                    data={ai.time_distribution.map((b) => ({
                      label: String(b.hour).padStart(2, "0"),
                      title: `${b.hour}:00–${b.hour}:59`,
                      value: b.value,
                    }))}
                    unit="people"
                    tickEvery={3}
                  />
                </ChartFrame>
                {ai.peak_hours.length > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Peak hours:{" "}
                    {ai.peak_hours
                      .map(
                        (p) =>
                          `${String(p.hour).padStart(2, "0")}:00 (${p.observations})`,
                      )
                      .join(", ")}
                  </p>
                )}
              </Panel>
              <Panel title="People by camera">
                <ChartFrame
                  table={
                    <DataTable
                      columns={["Camera", "Observations", "In", "Out"]}
                      rows={ai.cameras.map((c) => [
                        c.camera_name ?? c.camera_ref ?? "—",
                        c.observations,
                        c.inbound,
                        c.outbound,
                      ])}
                    />
                  }
                >
                  <BarList
                    rows={ai.cameras
                      .slice(0, 12)
                      .map((c) => ({
                        label: c.camera_name ?? c.camera_ref ?? "—",
                        value: c.observations,
                      }))}
                  />
                </ChartFrame>
              </Panel>
              <Panel title="People heatmap">
                <Heatmap
                  cells={ai.heatmap}
                  ariaLabel="People heatmap by weekday and hour"
                  unit="people"
                />
              </Panel>
            </div>
          </>
        )
      )}

      {/* ------------------------------------------------------------ reports */}
      <Panel title="Reports" icon={FileDown}>
        <p className="mb-3 text-xs text-muted-foreground">
          Daily / weekly / monthly reports cover the period ending today; zone,
          AI, device and camera reports use the period selected above.
          {selected ? ` Filter: ${selected.name}.` : ""}
        </p>
        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {REPORTS.map((r) => {
            const blocked =
              (r.needs === "camera" && !selected) ||
              (r.needs === "device" && !selected);
            return (
              <li
                key={r.kind}
                className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm"
              >
                <span>
                  {r.label}
                  {blocked && (
                    <span className="block text-[11px] text-muted-foreground">
                      select a camera
                    </span>
                  )}
                </span>
                <span className="flex gap-1">
                  {(["csv", "pdf"] as const).map((f) => (
                    <Button
                      key={f}
                      size="sm"
                      variant="outline"
                      disabled={blocked || busy != null}
                      onClick={() => downloadReport(r.kind, f)}
                      aria-label={`Download ${r.label} report as ${f.toUpperCase()}`}
                    >
                      <ArrowDownToLine
                        className={
                          busy === `${r.kind}.${f}` ? "animate-bounce" : ""
                        }
                      />
                      {f.toUpperCase()}
                    </Button>
                  ))}
                </span>
              </li>
            );
          })}
        </ul>
      </Panel>

      {data && (
        <details className="rounded-lg border bg-card p-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer font-medium text-foreground">
            How these numbers are computed (limitations)
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {Object.entries(data.limitations).map(([k, v]) => (
              <li key={k}>{v}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
