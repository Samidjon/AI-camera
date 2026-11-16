"use client";

import { Check, Copy, KeyRound, Loader2 } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { API_BASE_URL, ApiError } from "@/lib/api/client";
import { fleet, type EnrollmentToken } from "@/lib/api/fleet";

export function EnrollDialog({
  token,
  open,
  onOpenChange,
}: {
  token: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [uses, setUses] = useState(1);
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<EnrollmentToken | null>(null);
  const [copied, setCopied] = useState(false);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      setCreated(
        await fleet.createEnrollment(token, {
          name: name || undefined,
          max_uses: uses,
          expires_in_hours: hours,
        }),
      );
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Central server is unreachable.",
      );
    } finally {
      setBusy(false);
    }
  }

  const command = created?.token
    ? `sudo aci-agentctl register --server ${API_BASE_URL}`
    : "";

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setCreated(null);
          setCopied(false);
        }
        onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an edge device</DialogTitle>
          <DialogDescription>
            Create an enrollment token and enter it on the edge device. The
            device then gets its own credential; the token is only used for
            registration.
          </DialogDescription>
        </DialogHeader>
        {!created ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="enroll-name">Label (optional)</Label>
              <Input
                id="enroll-name"
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
                placeholder="Warehouse NVR"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="enroll-uses">Devices (uses)</Label>
                <Input
                  id="enroll-uses"
                  type="number"
                  min={1}
                  max={1000}
                  value={uses}
                  onChange={(e) => setUses(Number(e.target.value) || 1)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="enroll-hours">Valid for (hours)</Label>
                <Input
                  id="enroll-hours"
                  type="number"
                  min={1}
                  max={720}
                  value={hours}
                  onChange={(e) => setHours(Number(e.target.value) || 24)}
                />
              </div>
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <Alert>
              <AlertDescription>
                Copy the token now — it is shown only once and stored on the
                server as a hash.
              </AlertDescription>
            </Alert>
            <div className="flex gap-2">
              <Input
                readOnly
                value={created.token ?? ""}
                className="font-mono text-xs"
                aria-label="Enrollment token"
              />
              <Button
                variant="outline"
                size="icon"
                aria-label="Copy token"
                onClick={() => {
                  void navigator.clipboard?.writeText(created.token ?? "");
                  setCopied(true);
                }}
              >
                {copied ? (
                  <Check className="size-4" />
                ) : (
                  <Copy className="size-4" />
                )}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              On the edge device run (it asks for the token):
            </p>
            <pre className="rounded-md border bg-muted px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap">
              {command}
            </pre>
            <p className="text-xs text-muted-foreground">
              Or use the Devices → This agent tab on that device. The server
              must be reachable over HTTPS from the device.
            </p>
          </div>
        )}
        <DialogFooter>
          {!created ? (
            <Button onClick={create} disabled={busy}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <KeyRound className="size-4" />
              )}
              Create token
            </Button>
          ) : (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
