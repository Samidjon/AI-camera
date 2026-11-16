"use client";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Camera, CameraGroup } from "@/lib/agent/types";
import { formatDateTime } from "@/lib/format";

import { CameraStatusBadge, SOURCE_LABEL } from "./camera-status";

export function CameraTable({
  cameras,
  groups,
  onOpen,
}: {
  cameras: Camera[];
  groups: CameraGroup[];
  onOpen: (camera: Camera) => void;
}) {
  const groupName = (id: string | null) => groups.find((g) => g.id === id)?.name ?? "—";
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Camera</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Resolution</TableHead>
            <TableHead className="text-right">FPS</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Group</TableHead>
            <TableHead>Last frame</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cameras.map((c) => (
            <TableRow key={c.id} className="cursor-pointer" onClick={() => onOpen(c)}>
              <TableCell>
                <button
                  type="button"
                  className="text-left outline-none focus-visible:underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(c);
                  }}
                >
                  <div className="font-medium">{c.name}</div>
                  <div className="max-w-[260px] truncate text-xs text-muted-foreground">{c.location ?? c.source}</div>
                </button>
              </TableCell>
              <TableCell>
                <CameraStatusBadge status={c.status} />
              </TableCell>
              <TableCell className="tabular text-right font-mono text-xs">
                {c.resolution ? `${c.resolution.width}×${c.resolution.height}` : "—"}
              </TableCell>
              <TableCell className="tabular text-right font-mono text-xs">{c.fps != null ? c.fps.toFixed(1) : "—"}</TableCell>
              <TableCell className="text-xs">{SOURCE_LABEL[c.source_type]}</TableCell>
              <TableCell className="text-xs">{groupName(c.group_id)}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{formatDateTime(c.last_frame_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
