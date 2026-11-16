"use client";

import { Cctv, FolderCog, LayoutGrid, List, Plus, RefreshCw, ServerOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { CameraDetailsSheet } from "@/components/cameras/camera-details-sheet";
import { CameraFormDialog } from "@/components/cameras/camera-form-dialog";
import { STATUS_META } from "@/components/cameras/camera-status";
import { CameraTable } from "@/components/cameras/camera-table";
import { CameraTile } from "@/components/cameras/camera-tile";
import { DeleteCameraDialog } from "@/components/cameras/delete-camera-dialog";
import { GroupsDialog } from "@/components/cameras/groups-dialog";
import { PageHeader } from "@/components/common/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useCameras } from "@/hooks/use-cameras";
import { AGENT_BASE_URL } from "@/lib/agent/client";
import type { CameraStatus } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import { usePermissions } from "@/lib/auth/permissions";
import { formatTime } from "@/lib/format";

/** Browsers allow ~6 HTTP/1.1 connections per host; keep room for API calls. */
const MAX_LIVE_PREVIEWS = 4;
const ALL = "__all__";
const UNGROUPED = "__none__";

export function CamerasView() {
  const { can } = usePermissions();
  const { logout } = useAuth();
  const router = useRouter();
  const { cameras, groups, loaded, error, updatedAt, refresh } = useCameras();
  const [view, setView] = useState<"grid" | "list">("grid");
  const [groupFilter, setGroupFilter] = useState(ALL);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [form, setForm] = useState<{ open: boolean; editId: string | null; key: number }>({ open: false, editId: null, key: 0 });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [groupsOpen, setGroupsOpen] = useState(false);
  const [liveOverride, setLiveOverride] = useState<string[]>([]);

  const visible = useMemo(
    () =>
      cameras.filter((c) =>
        groupFilter === ALL ? true : groupFilter === UNGROUPED ? !c.group_id : c.group_id === groupFilter,
      ),
    [cameras, groupFilter],
  );

  // Live previews: tiles the operator explicitly opened first, then the first online cameras.
  const liveIds = useMemo(() => {
    const online = visible.filter((c) => c.status === "online").map((c) => c.id);
    const ordered = [...liveOverride.filter((id) => online.includes(id)), ...online.filter((id) => !liveOverride.includes(id))];
    return new Set(ordered.slice(0, MAX_LIVE_PREVIEWS));
  }, [visible, liveOverride]);

  const selected = cameras.find((c) => c.id === selectedId) ?? null;
  const editing = cameras.find((c) => c.id === form.editId);
  const counts = cameras.reduce<Partial<Record<CameraStatus, number>>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1;
    return acc;
  }, {});
  const stale = Boolean(error && loaded);
  const authProblem = error instanceof ApiError && (error.status === 401 || error.status === 503);
  // 503: agent has no secret; "Invalid token": secrets differ; anything else 401: token expired/outdated.
  const authHint: "config" | "relogin" | null = !authProblem
    ? null
    : error.status === 503 || error.message === "Invalid token"
      ? "config"
      : "relogin";

  const openDetails = (id: string) => {
    setSelectedId(id);
    setDetailsOpen(true);
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Cameras"
        description={`Edge agent ${AGENT_BASE_URL}${updatedAt ? ` · updated ${formatTime(updatedAt)}` : ""}`}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refresh} aria-label="Refresh camera list">
              <RefreshCw />
            </Button>
            {can("cameras:manage") && (
              <>
                <Button variant="outline" size="sm" onClick={() => setGroupsOpen(true)}>
                  <FolderCog /> Groups
                </Button>
                <Button size="sm" onClick={() => setForm((f) => ({ open: true, editId: null, key: f.key + 1 }))}>
                  <Plus /> Add camera
                </Button>
              </>
            )}
          </>
        }
      />

      {error && (
        <Alert variant="destructive">
          <ServerOff />
          <AlertTitle>{authProblem ? "Edge agent rejected the request" : "Edge agent unreachable"}</AlertTitle>
          <AlertDescription>
            <p>
              {error.message}
              {stale && ` Showing data from ${formatTime(updatedAt)}; statuses may be out of date.`}
            </p>
            {authHint === "config" && (
              <p>Set ACI_AGENT_JWT_SECRET on the agent to the same value as the server&apos;s ACI_JWT_SECRET.</p>
            )}
            {authHint === "relogin" && (
              <Button
                size="sm"
                variant="outline"
                className="mt-2"
                onClick={() => {
                  logout();
                  router.replace("/login");
                }}
              >
                Sign in again
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-live="polite">
          <span>
            <span className="font-mono text-foreground">{cameras.length}</span> cameras
          </span>
          {(Object.keys(STATUS_META) as CameraStatus[])
            .filter((s) => counts[s])
            .map((s) => (
              <span key={s}>
                <span className="font-mono text-foreground">{counts[s]}</span> {STATUS_META[s].label.toLowerCase()}
              </span>
            ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Select value={groupFilter} onValueChange={setGroupFilter}>
            <SelectTrigger size="sm" className="w-44" aria-label="Filter by group">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All groups</SelectItem>
              <SelectItem value={UNGROUPED}>Ungrouped</SelectItem>
              {groups.map((g) => (
                <SelectItem key={g.id} value={g.id}>
                  {g.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            value={view}
            onValueChange={(v) => v && setView(v as "grid" | "list")}
            aria-label="Layout"
          >
            <ToggleGroupItem value="grid" aria-label="Grid view">
              <LayoutGrid />
            </ToggleGroupItem>
            <ToggleGroupItem value="list" aria-label="List view">
              <List />
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>

      {!loaded && !error ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="aspect-video rounded-lg" />
          ))}
        </div>
      ) : visible.length === 0 && loaded ? (
        <div className="grid place-items-center rounded-lg border border-dashed px-6 py-16 text-center">
          <Cctv className="size-8 text-muted-foreground" aria-hidden />
          <h2 className="mt-3 text-sm font-semibold">{cameras.length ? "No cameras in this group" : "No cameras yet"}</h2>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            Add an RTSP or ONVIF IP camera, a USB webcam or a video file. Streams are processed locally on the edge device.
          </p>
          {can("cameras:manage") && !cameras.length && (
            <Button className="mt-4" onClick={() => setForm((f) => ({ open: true, editId: null, key: f.key + 1 }))}>
              <Plus /> Add camera
            </Button>
          )}
        </div>
      ) : view === "grid" ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {visible.map((c) => (
              <CameraTile
                key={c.id}
                camera={c}
                live={liveIds.has(c.id)}
                onRequestLive={() => setLiveOverride((ids) => [c.id, ...ids.filter((x) => x !== c.id)])}
                onOpen={() => openDetails(c.id)}
              />
            ))}
          </div>
          {visible.filter((c) => c.status === "online").length > MAX_LIVE_PREVIEWS && (
            <p className="text-xs text-muted-foreground">
              Live preview is limited to {MAX_LIVE_PREVIEWS} cameras at a time in this version; click a paused tile to
              switch. Multi-camera live walls arrive with the monitoring screen (MVP 07).
            </p>
          )}
        </>
      ) : (
        <CameraTable cameras={visible} groups={groups} onOpen={(c) => openDetails(c.id)} />
      )}

      <CameraDetailsSheet
        camera={selected}
        groups={groups}
        open={detailsOpen && Boolean(selected)}
        onOpenChange={setDetailsOpen}
        onChanged={refresh}
        onEdit={() => {
          setDetailsOpen(false);
          setForm((f) => ({ open: true, editId: selectedId, key: f.key + 1 }));
        }}
        onDelete={() => setDeleteOpen(true)}
      />
      {form.open && (
        <CameraFormDialog
          key={form.key}
          open={form.open}
          onOpenChange={(open) => setForm((f) => ({ ...f, open }))}
          camera={editing}
          groups={groups}
          onSaved={refresh}
        />
      )}
      <DeleteCameraDialog
        camera={selected}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onDeleted={() => {
          setDetailsOpen(false);
          setSelectedId(null);
          refresh();
        }}
      />
      <GroupsDialog open={groupsOpen} onOpenChange={setGroupsOpen} groups={groups} onChanged={refresh} />
    </div>
  );
}
