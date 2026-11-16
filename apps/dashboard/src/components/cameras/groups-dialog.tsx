"use client";

import { Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { agent } from "@/lib/agent/client";
import type { CameraGroup } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";

export function GroupsDialog({
  open,
  onOpenChange,
  groups,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: CameraGroup[];
  onChanged: () => void;
}) {
  const { token } = usePermissions();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Edge agent is unreachable.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Camera groups</DialogTitle>
          <DialogDescription>Deleting a group keeps its cameras (they become ungrouped).</DialogDescription>
        </DialogHeader>
        <form
          className="flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (token && name.trim() && (await act("new", () => agent.createGroup(token, name.trim())))) setName("");
          }}
        >
          <Input placeholder="New group name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} aria-label="New group name" />
          <Button type="submit" disabled={!name.trim() || busy !== null}>
            {busy === "new" ? <Loader2 className="animate-spin" /> : <Plus />}
            Add
          </Button>
        </form>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <ul className="divide-y rounded-md border">
          {groups.length === 0 && <li className="px-3 py-4 text-center text-sm text-muted-foreground">No groups yet.</li>}
          {groups.map((g) => (
            <li key={g.id} className="flex items-center gap-3 px-3 py-2">
              <span className="flex-1 truncate text-sm">{g.name}</span>
              <span className="font-mono text-xs text-muted-foreground">{g.camera_count} cam</span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete group ${g.name}`}
                disabled={busy !== null}
                onClick={() => token && act(g.id, () => agent.deleteGroup(token, g.id))}
              >
                {busy === g.id ? <Loader2 className="animate-spin" /> : <Trash2 />}
              </Button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
