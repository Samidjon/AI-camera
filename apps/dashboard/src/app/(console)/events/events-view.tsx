"use client";

import {
  Activity,
  ArrowRightLeft,
  CircleAlert,
  CircleCheck,
  LogIn,
  LogOut,
  Move,
  RefreshCw,
  Square,
  UserCheck,
  UserMinus,
  UserRoundSearch,
} from "lucide-react";
import { useCallback, useState } from "react";

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCameras } from "@/hooks/use-cameras";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { AgentEvent, AgentEventType } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";

const TYPE_META: Record<
  AgentEventType,
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  motion_started: { label: "Motion started", icon: Move },
  motion_ended: { label: "Motion ended", icon: Square },
  object_entered_zone: { label: "Entered zone", icon: LogIn },
  object_exited_zone: { label: "Exited zone", icon: LogOut },
  line_crossed: { label: "Line crossed", icon: ArrowRightLeft },
  camera_motion_activity: { label: "Motion activity", icon: Activity },
  person_detected: { label: "Person detected (AI)", icon: UserRoundSearch },
  person_left: { label: "Person left (AI)", icon: UserMinus },
  person_crossed_line: { label: "Person crossed line (AI)", icon: UserCheck },
  camera_error: { label: "Camera error", icon: CircleAlert },
  camera_recovered: { label: "Camera recovered", icon: CircleCheck },
  ai_error: { label: "AI error", icon: CircleAlert },
  ai_recovered: { label: "AI recovered", icon: CircleCheck },
};

const TYPE_FILTERS: Record<string, AgentEventType[] | undefined> = {
  all: undefined,
  motion: ["motion_started", "motion_ended"],
  zones: ["object_entered_zone", "object_exited_zone"],
  lines: ["line_crossed"],
  activity: ["camera_motion_activity"],
  people: ["person_detected", "person_left", "person_crossed_line"],
  errors: ["camera_error", "camera_recovered", "ai_error", "ai_recovered"],
};

const PERIODS: Record<string, number> = {
  "1h": 1,
  "24h": 24,
  "7d": 168,
  "30d": 720,
};
const ALL = "__all__";

/** Rolling window start, computed at request time (not during render). */
function sinceFor(period: string): string {
  return new Date(Date.now() - PERIODS[period] * 3_600_000).toISOString();
}
const PAGE = 50;

function describe(e: AgentEvent): string {
  const m = e.metadata as Record<
    string,
    string | number | boolean | null | undefined
  >;
  const parts: string[] = [];
  if (m.zone_name) parts.push(`zone “${m.zone_name}”`);
  if (m.line_name)
    parts.push(
      `line “${m.line_name}” · ${m.direction === "forward" ? "forward" : "backward"}`,
    );
  if (e.event_type === "motion_started")
    parts.push(`${m.objects ?? 0} motion object(s)`);
  if (e.event_type === "motion_ended")
    parts.push(
      `lasted ${m.duration_s ?? 0}s · max ${m.max_objects ?? 0} object(s)`,
    );
  if (e.event_type === "object_exited_zone" && m.dwell_s != null)
    parts.push(`${m.dwell_s}s in zone`);
  if (e.event_type === "camera_motion_activity") {
    parts.push(
      `active ${Math.round(Number(m.active_ratio ?? 0) * 100)}% of ${m.interval_s}s`,
    );
  }
  if (e.source === "ai") {
    if (m.confidence != null)
      parts.push(`confidence ${Number(m.confidence).toFixed(2)}`);
    if (e.event_type === "person_left" && m.duration_s != null)
      parts.push(`visible ${m.duration_s}s`);
    const attrs = m.attributes as unknown as
      Record<string, { value: string }> | undefined;
    if (attrs) {
      const shown = [
        "upper_color",
        "lower_color",
        "backpack",
        "bag",
        "headwear",
      ]
        .filter(
          (k) =>
            attrs[k] && attrs[k].value !== "unknown" && attrs[k].value !== "no",
        )
        .map((k) =>
          attrs[k].value === "yes"
            ? k
            : `${k.replace("_color", "")} ${attrs[k].value}`,
        );
      if (shown.length) parts.push(shown.join(", "));
    }
  }
  if (m.heading) parts.push(`heading ${m.heading}`);
  if (m.reason && m.reason !== "no motion") parts.push(String(m.reason));
  return parts.join(" · ");
}

/** Events stored on the edge agent this dashboard is connected to (works without the central server). */
export function EventsView({ embedded = false }: { embedded?: boolean }) {
  const { token } = usePermissions();
  const { cameras } = useCameras();
  const [cameraId, setCameraId] = useState(ALL);
  const [type, setType] = useState("all");
  const [period, setPeriod] = useState("24h");
  const [older, setOlder] = useState<{
    items: AgentEvent[];
    cursor: string | null;
  } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchFirst = useCallback(
    (signal: AbortSignal) =>
      token
        ? agent.events(
            token,
            {
              camera_id: cameraId === ALL ? undefined : cameraId,
              event_type: TYPE_FILTERS[type],
              since: sinceFor(period),
              limit: PAGE,
            },
            signal,
          )
        : Promise.resolve({ items: [], next_cursor: null }),
    [token, cameraId, type, period],
  );
  const first = usePolling(fetchFirst, 5_000);

  const filterKey = `${cameraId}|${type}|${period}`;
  const [olderKey, setOlderKey] = useState(filterKey);
  if (olderKey !== filterKey) {
    setOlderKey(filterKey);
    setOlder(null); // filters changed: drop previously loaded pages
  }

  const items = [...(first.data?.items ?? []), ...(older?.items ?? [])];
  const nextCursor = older ? older.cursor : (first.data?.next_cursor ?? null);
  const cameraName = (id: string) =>
    cameras.find((c) => c.id === id)?.name ?? id.slice(0, 8);

  async function loadMore() {
    if (!token || !nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await agent.events(token, {
        camera_id: cameraId === ALL ? undefined : cameraId,
        event_type: TYPE_FILTERS[type],
        since: sinceFor(period),
        cursor: nextCursor,
        limit: PAGE,
      });
      setOlder((o) => ({
        items: [...(o?.items ?? []), ...page.items],
        cursor: page.next_cursor,
      }));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {embedded ? (
        <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>
            Stored on this edge agent — available even when the central server
            is down.
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={first.refresh}
            aria-label="Refresh local events"
          >
            <RefreshCw />
          </Button>
        </div>
      ) : (
        <PageHeader
          title="Events"
          description="Stored locally on the edge device. STANDARD = motion analysis without AI (motion objects, not people); AI = optional person detection."
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={first.refresh}
              aria-label="Refresh events"
            >
              <RefreshCw />
            </Button>
          }
        />
      )}

      {first.error && (
        <Alert variant="destructive">
          <AlertTitle>Edge agent unreachable</AlertTitle>
          <AlertDescription>{first.error.message}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap gap-2">
        <Select value={cameraId} onValueChange={setCameraId}>
          <SelectTrigger size="sm" className="w-48" aria-label="Camera">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All cameras</SelectItem>
            {cameras.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={type} onValueChange={setType}>
          <SelectTrigger size="sm" className="w-44" aria-label="Event type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All event types</SelectItem>
            <SelectItem value="motion">Motion</SelectItem>
            <SelectItem value="zones">Zone enter / exit</SelectItem>
            <SelectItem value="lines">Line crossing</SelectItem>
            <SelectItem value="activity">Activity summaries</SelectItem>
            <SelectItem value="people">People (AI)</SelectItem>
            <SelectItem value="errors">Camera / AI errors</SelectItem>
          </SelectContent>
        </Select>
        <Select value={period} onValueChange={setPeriod}>
          <SelectTrigger size="sm" className="w-36" aria-label="Period">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="1h">Last hour</SelectItem>
            <SelectItem value="24h">Last 24 hours</SelectItem>
            <SelectItem value="7d">Last 7 days</SelectItem>
            <SelectItem value="30d">Last 30 days</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {first.loading && !first.data ? (
        <Skeleton className="h-64 rounded-lg" />
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed px-6 py-14 text-center text-sm text-muted-foreground">
          No events for these filters. Events appear when the Standard Vision
          Engine detects motion, zone activity or line crossings on an online
          camera.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">Time</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Camera</TableHead>
                <TableHead>Details</TableHead>
                <TableHead>Track</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((e) => {
                const meta = TYPE_META[e.event_type] ?? {
                  label: e.event_type,
                  icon: Activity,
                };
                const Icon = meta.icon;
                return (
                  <TableRow key={e.id}>
                    <TableCell className="tabular font-mono text-xs text-muted-foreground">
                      {formatDateTime(e.occurred_at)}
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-2 text-sm">
                        <Icon
                          className="size-3.5 text-muted-foreground"
                          aria-hidden
                        />
                        {meta.label}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      {cameraName(e.camera_id)}
                    </TableCell>
                    <TableCell className="max-w-[360px] truncate text-xs text-muted-foreground">
                      {describe(e)}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {e.track_id ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className="font-mono text-[10px] uppercase"
                      >
                        {e.source}
                      </Badge>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {nextCursor && (
        <Button
          variant="outline"
          className="self-center"
          onClick={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading…" : "Load older events"}
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        Track IDs are temporary and camera-scoped; they never identify a person.
        Synchronization to the central server and CSV export arrive in MVP
        06–08.
      </p>
    </div>
  );
}
