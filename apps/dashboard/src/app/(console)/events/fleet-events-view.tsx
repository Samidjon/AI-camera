"use client";

import {
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ApiError } from "@/lib/api/client";
import {
  EVENT_CATEGORIES,
  EVENT_LABELS,
  platform,
  type EventQuery,
  type RegistryCamera,
  type ServerEvent,
} from "@/lib/api/platform";
import { usePermissions } from "@/lib/auth/permissions";
import { formatDateTime } from "@/lib/format";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";

const ALL = "__all__";
const PAGE_SIZE = 50;
const PERIODS: Record<string, number | null> = {
  "1h": 1,
  "24h": 24,
  "7d": 168,
  "30d": 720,
  custom: null,
};

const SOURCE_BADGE: Record<
  ServerEvent["source"],
  { label: string; variant: "default" | "secondary" | "outline" }
> = {
  standard: { label: "STANDARD", variant: "outline" },
  ai: { label: "AI", variant: "default" },
  system: { label: "SYSTEM", variant: "secondary" },
};

function describe(e: ServerEvent): string {
  const m = e.metadata as Record<string, unknown>;
  const parts: string[] = [];
  if (e.zone_id) parts.push(`zone ${(m.zone_name as string) ?? e.zone_id}`);
  if (e.line_id)
    parts.push(
      `line ${(m.line_name as string) ?? e.line_id}${m.direction ? ` (${m.direction})` : ""}`,
    );
  if (e.track_id)
    parts.push(`${e.source === "ai" ? "person" : "track"} #${e.track_id}`);
  if (typeof m.confidence === "number")
    parts.push(`${Math.round(m.confidence * 100)}%`);
  if (typeof m.duration_s === "number") parts.push(`${m.duration_s}s`);
  if (m.failure_kind) parts.push(String(m.failure_kind));
  if (m.reason) parts.push(String(m.reason));
  if (m.error && !m.failure_kind) parts.push(String(m.error));
  return parts.join(" · ");
}

/** datetime-local value ↔ ISO (the input works in the viewer's local time). */
function toLocalInput(d: Date): string {
  const off = d.getTimezoneOffset() * 60_000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
}

export function FleetEventsView() {
  const { token, can } = usePermissions();
  const [categories, setCategories] = useState<string[]>([]);
  const [camera, setCamera] = useState(ALL);
  const [period, setPeriod] = useState("24h");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState(""); // debounced search
  const [cameras, setCameras] = useState<RegistryCamera[]>([]);
  const [pages, setPages] = useState<(string | null)[]>([null]); // cursor stack
  const [items, setItems] = useState<ServerEvent[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setQuery(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    if (!token) return;
    platform.cameras(token).then(setCameras, () => setCameras([]));
  }, [token]);

  const filters = useMemo((): EventQuery => {
    const [deviceId, cameraRef] =
      camera === ALL ? [undefined, undefined] : camera.split("|");
    return {
      category: categories,
      device_id: deviceId,
      camera_ref: cameraRef,
      q: query,
    };
  }, [categories, camera, query]);

  /** Time window, computed when the request is made (never during render). */
  const windowFor = useCallback((): Pick<EventQuery, "since" | "until"> => {
    if (period === "custom") {
      return {
        since: from ? new Date(from).toISOString() : undefined,
        until: to ? new Date(to).toISOString() : undefined,
      };
    }
    return {
      since: new Date(
        Date.now() - (PERIODS[period] ?? 24) * 3_600_000,
      ).toISOString(),
    };
  }, [period, from, to]);

  const page = pages.length - 1;
  const cursor = pages[page];

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    void Promise.resolve().then(async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await platform.events(
          token,
          { ...filters, ...windowFor() },
          cursor,
          PAGE_SIZE,
          controller.signal,
        );
        setItems(r.items);
        setNext(r.next_cursor);
        setFresh(0);
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            e instanceof ApiError
              ? e.message
              : "Central server is unreachable.",
          );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    });
    return () => controller.abort();
  }, [token, filters, windowFor, cursor, reload]);

  // New events arrive over the WebSocket: count them (the list stays stable while reading).
  useLiveMessages(["event.created"], (m) =>
    setFresh((n) => n + (Number(m.data.count) || 0)),
  );

  const resetPaging = () => setPages([null]);

  async function exportCsv() {
    if (!token) return;
    setExporting(true);
    try {
      const { blob, filename } = await platform.exportCsv(token, {
        ...filters,
        ...windowFor(),
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Export failed — central server unreachable.",
      );
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border bg-card p-3">
        <ToggleGroup
          type="multiple"
          variant="outline"
          size="sm"
          value={categories}
          onValueChange={(v) => {
            setCategories(v);
            resetPaging();
          }}
          className="flex-wrap justify-start"
          aria-label="Event types"
        >
          {EVENT_CATEGORIES.map((c) => (
            <ToggleGroupItem
              key={c.key}
              value={c.key}
              data-testid={`category-${c.key}`}
            >
              {c.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex flex-wrap items-end gap-3">
          <div className="relative min-w-56 flex-1">
            <Search
              className="absolute top-2.5 left-2.5 size-4 text-muted-foreground"
              aria-hidden
            />
            <Input
              className="pl-8"
              placeholder="Search camera, zone, line, track, details…"
              aria-label="Search events"
              value={search}
              maxLength={100}
              onChange={(e) => {
                setSearch(e.target.value);
                resetPaging();
              }}
            />
          </div>
          <Select
            value={camera}
            onValueChange={(v) => {
              setCamera(v);
              resetPaging();
            }}
          >
            <SelectTrigger className="w-56" aria-label="Camera filter">
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
          <Select
            value={period}
            onValueChange={(v) => {
              setPeriod(v);
              if (v === "custom" && !from)
                setFrom(toLocalInput(new Date(Date.now() - 86_400_000)));
              resetPaging();
            }}
          >
            <SelectTrigger className="w-36" aria-label="Period">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1h">Last hour</SelectItem>
              <SelectItem value="24h">Last 24 hours</SelectItem>
              <SelectItem value="7d">Last 7 days</SelectItem>
              <SelectItem value="30d">Last 30 days</SelectItem>
              <SelectItem value="custom">Custom range</SelectItem>
            </SelectContent>
          </Select>
          {period === "custom" && (
            <>
              <div className="flex flex-col gap-1">
                <Label htmlFor="ev-from" className="text-xs">
                  From
                </Label>
                <Input
                  id="ev-from"
                  type="datetime-local"
                  value={from}
                  onChange={(e) => (setFrom(e.target.value), resetPaging())}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="ev-to" className="text-xs">
                  To
                </Label>
                <Input
                  id="ev-to"
                  type="datetime-local"
                  value={to}
                  onChange={(e) => (setTo(e.target.value), resetPaging())}
                />
              </div>
            </>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => (resetPaging(), setReload((n) => n + 1))}
            aria-label="Refresh events"
          >
            <RefreshCw /> Refresh
          </Button>
          {can("events:export") && (
            <Button
              variant="outline"
              size="sm"
              onClick={exportCsv}
              disabled={exporting}
            >
              {exporting ? <Loader2 className="animate-spin" /> : <Download />}{" "}
              Export CSV
            </Button>
          )}
        </div>
      </div>

      {fresh > 0 && page === 0 && (
        <button
          type="button"
          onClick={() => setReload((n) => n + 1)}
          className="rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-left text-sm"
          data-testid="new-events"
        >
          {fresh} new event{fresh === 1 ? "" : "s"} — show
        </button>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Events unavailable</AlertTitle>
          <AlertDescription>
            {error} The “This agent” tab still shows events stored on the local
            edge device.
          </AlertDescription>
        </Alert>
      )}

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Time</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>Camera</TableHead>
              <TableHead>Device</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody data-testid="events-table">
            {loading && items.length === 0
              ? Array.from({ length: 6 }, (_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={5}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              : items.map((e) => (
                  <TableRow key={e.id} data-event-type={e.event_type}>
                    <TableCell className="font-mono text-xs">
                      {formatDateTime(e.occurred_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={SOURCE_BADGE[e.source].variant}
                          className="font-mono text-[10px]"
                        >
                          {SOURCE_BADGE[e.source].label}
                        </Badge>
                        <span className="text-sm">
                          {EVENT_LABELS[e.event_type] ?? e.event_type}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm">
                      {e.camera_name ?? e.camera_ref ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {e.device_name ?? "—"}
                    </TableCell>
                    <TableCell
                      className="max-w-96 truncate text-xs text-muted-foreground"
                      title={describe(e)}
                    >
                      {describe(e)}
                    </TableCell>
                  </TableRow>
                ))}
            {!loading && items.length === 0 && !error && (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  No events match these filters.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-muted-foreground">
          <span>
            Page {page + 1} · {items.length} events
            {loading ? " · loading…" : ""}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0 || loading}
              onClick={() => setPages((p) => p.slice(0, -1))}
            >
              <ChevronLeft /> Newer
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!next || loading}
              onClick={() => setPages((p) => [...p, next])}
            >
              Older <ChevronRight />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
