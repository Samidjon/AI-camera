"use client";

import { Loader2, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { agent } from "@/lib/agent/client";
import type { Camera } from "@/lib/agent/types";
import { ApiError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";

export function DeleteCameraDialog({
  camera,
  open,
  onOpenChange,
  onDeleted,
}: {
  camera: Camera | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: (id: string) => void;
}) {
  const { token } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    if (!camera || !token) return;
    setBusy(true);
    setError(null);
    try {
      await agent.deleteCamera(token, camera.id);
      onDeleted(camera.id);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Edge agent is unreachable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-destructive/10 text-destructive">
            <Trash2 />
          </AlertDialogMedia>
          <AlertDialogTitle>Delete “{camera?.name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            The stream is stopped and the camera, its settings and stored credentials are removed from this edge
            device. This can&apos;t be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          {/* Plain button (not AlertDialogAction) so the dialog stays open until deletion succeeds. */}
          <Button variant="destructive" onClick={confirm} disabled={busy}>
            {busy && <Loader2 className="animate-spin" />}
            Delete camera
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
