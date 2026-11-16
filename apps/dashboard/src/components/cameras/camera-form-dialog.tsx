"use client";

import { CheckCircle2, Loader2, PlugZap, XCircle } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { agent } from "@/lib/agent/client";
import type { Camera, CameraGroup, CameraInput, CameraUpdate, SourceType, TestResult } from "@/lib/agent/types";
import { ApiError, NetworkError } from "@/lib/api/client";
import { usePermissions } from "@/lib/auth/permissions";

import { FAILURE_HINT } from "./camera-status";

const SOURCE_FIELDS: Record<SourceType, { label: string; placeholder: string; help: string; creds: boolean }> = {
  rtsp: {
    label: "RTSP URL",
    placeholder: "rtsp://192.168.1.64:554/Streaming/Channels/101",
    help: "Credentials typed into the URL are moved to encrypted storage automatically.",
    creds: true,
  },
  onvif: {
    label: "ONVIF device (host:port)",
    placeholder: "192.168.1.64:80",
    help: "The RTSP stream address is discovered from the device via ONVIF.",
    creds: true,
  },
  usb: { label: "USB device index", placeholder: "0", help: "0 is the first camera connected to the edge device.", creds: false },
  file: {
    label: "Video file path (on the edge device)",
    placeholder: "C:\\videos\\entrance.mp4",
    help: "Useful for testing and replay. MP4, MKV, AVI, MOV, WEBM, TS.",
    creds: false,
  },
  ffmpeg: {
    label: "Stream URL",
    placeholder: "http://192.168.1.70/video.mjpg",
    help: "Any FFmpeg-readable network stream: HTTP(S) MJPEG, RTMP, SRT, UDP/RTP.",
    creds: true,
  },
};

const NO_GROUP = "__none__";

interface FormState {
  name: string;
  location: string;
  group_id: string;
  source_type: SourceType;
  source_uri: string;
  username: string;
  password: string;
  clear_credentials: boolean;
  max_fps: string;
  loop: boolean;
  onvif_profile: string;
  is_enabled: boolean;
  connect: boolean;
}

function initialState(camera?: Camera): FormState {
  return {
    name: camera?.name ?? "",
    location: camera?.location ?? "",
    group_id: camera?.group_id ?? NO_GROUP,
    source_type: camera?.source_type ?? "rtsp",
    source_uri: camera ? (camera.source_type === "usb" ? camera.source.replace(/^usb:/, "") : camera.source) : "",
    username: "",
    password: "",
    clear_credentials: false,
    max_fps: camera?.options.max_fps != null ? String(camera.options.max_fps) : "",
    loop: camera?.options.loop ?? true,
    onvif_profile: String(camera?.options.onvif_profile ?? 0),
    is_enabled: camera?.is_enabled ?? true,
    connect: true,
  };
}

function errorText(err: unknown): string {
  if (err instanceof NetworkError) return "Edge agent is unreachable.";
  if (err instanceof ApiError) {
    const details = Array.isArray(err.details)
      ? (err.details as { loc: string[]; msg: string }[]).map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")
      : "";
    return details ? `${err.message} — ${details}` : err.message;
  }
  return "Unexpected error.";
}

export function CameraFormDialog({
  open,
  onOpenChange,
  camera,
  groups,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  camera?: Camera;
  groups: CameraGroup[];
  onSaved: (camera: Camera) => void;
}) {
  const { token } = usePermissions();
  const [form, setForm] = useState<FormState>(() => initialState(camera));
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const isEdit = Boolean(camera);
  const field = SOURCE_FIELDS[form.source_type];

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (["source_type", "source_uri", "username", "password", "onvif_profile"].includes(key)) setTest(null);
  };

  const options = () => ({
    max_fps: form.max_fps ? Number(form.max_fps) : null,
    ...(form.source_type === "file" ? { loop: form.loop } : {}),
    ...(form.source_type === "onvif" ? { onvif_profile: Number(form.onvif_profile) } : {}),
  });

  const credentials = (): { username?: string; password?: string } => {
    if (!field.creds) return {};
    if (!form.username && !form.password) return {};
    return { username: form.username, password: form.password };
  };

  const sourceChanged = camera
    ? form.source_type !== camera.source_type || form.source_uri !== initialState(camera).source_uri
    : true;

  async function runTest() {
    if (!token) return;
    setTesting(true);
    setError(null);
    setTest(null);
    try {
      const creds = credentials();
      const result =
        camera && !sourceChanged && !creds.password && !form.clear_credentials
          ? await agent.testCamera(token, camera.id)
          : await agent.testInput(token, {
              source_type: form.source_type,
              source_uri: form.source_uri,
              options: options(),
              ...creds,
            });
      setTest(result);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setTesting(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    setError(null);
    const base = {
      name: form.name.trim(),
      location: form.location.trim() || null,
      group_id: form.group_id === NO_GROUP ? null : form.group_id,
      source_type: form.source_type,
      source_uri: form.source_uri.trim(),
      options: options(),
      is_enabled: form.is_enabled,
    };
    try {
      let saved: Camera;
      if (camera) {
        const update: CameraUpdate = { ...base, ...credentials() };
        if (form.clear_credentials) update.clear_credentials = true;
        saved = await agent.updateCamera(token, camera.id, update);
      } else {
        const input: CameraInput = { ...base, ...credentials(), connect: form.connect };
        saved = await agent.createCamera(token, input);
      }
      onSaved(saved);
      onOpenChange(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit camera" : "Add camera"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "Changing the source or credentials reconnects the camera." : "Connect an IP, ONVIF, USB or file source."}
          </DialogDescription>
        </DialogHeader>

        <form id="camera-form" onSubmit={onSubmit} className="grid gap-4" autoComplete="off">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="cam-name">Name</Label>
              <Input id="cam-name" required maxLength={120} value={form.name} onChange={(e) => set("name", e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="cam-location">Location</Label>
              <Input
                id="cam-location"
                maxLength={255}
                placeholder="e.g. Main entrance"
                value={form.location}
                onChange={(e) => set("location", e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="cam-type">Source type</Label>
              <Select value={form.source_type} onValueChange={(v) => set("source_type", v as SourceType)}>
                <SelectTrigger id="cam-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="rtsp">RTSP IP camera</SelectItem>
                  <SelectItem value="onvif">ONVIF camera</SelectItem>
                  <SelectItem value="usb">USB webcam</SelectItem>
                  <SelectItem value="file">Video file</SelectItem>
                  <SelectItem value="ffmpeg">Other stream (FFmpeg)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="cam-group">Group</Label>
              <Select value={form.group_id} onValueChange={(v) => set("group_id", v)}>
                <SelectTrigger id="cam-group" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_GROUP}>No group</SelectItem>
                  {groups.map((g) => (
                    <SelectItem key={g.id} value={g.id}>
                      {g.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="cam-source">{field.label}</Label>
            <Input
              id="cam-source"
              required
              className="font-mono text-xs"
              placeholder={field.placeholder}
              value={form.source_uri}
              onChange={(e) => set("source_uri", e.target.value)}
              aria-describedby="cam-source-help"
            />
            <p id="cam-source-help" className="text-xs text-muted-foreground">
              {field.help}
            </p>
          </div>

          {field.creds && (
            <fieldset className="grid gap-3 rounded-md border p-3">
              <legend className="px-1 text-xs text-muted-foreground">Credentials (stored encrypted on the edge device)</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="cam-user">Username</Label>
                  <Input
                    id="cam-user"
                    autoComplete="off"
                    placeholder={camera?.has_credentials ? "Unchanged" : ""}
                    value={form.username}
                    disabled={form.clear_credentials}
                    onChange={(e) => set("username", e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="cam-pass">Password</Label>
                  <Input
                    id="cam-pass"
                    type="password"
                    autoComplete="new-password"
                    placeholder={camera?.has_credentials ? "••••••••  (unchanged)" : ""}
                    value={form.password}
                    disabled={form.clear_credentials}
                    onChange={(e) => set("password", e.target.value)}
                  />
                </div>
              </div>
              {camera?.has_credentials && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Switch aria-label="Remove stored credentials" checked={form.clear_credentials} onCheckedChange={(v) => set("clear_credentials", v)} />
                  Remove stored credentials
                </label>
              )}
            </fieldset>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="cam-fps">Max processing FPS</Label>
              <Input
                id="cam-fps"
                type="number"
                min={0.5}
                max={120}
                step={0.5}
                placeholder="Source rate"
                value={form.max_fps}
                onChange={(e) => set("max_fps", e.target.value)}
              />
            </div>
            {form.source_type === "onvif" && (
              <div className="grid gap-2">
                <Label htmlFor="cam-profile">ONVIF profile #</Label>
                <Input
                  id="cam-profile"
                  type="number"
                  min={0}
                  max={16}
                  value={form.onvif_profile}
                  onChange={(e) => set("onvif_profile", e.target.value)}
                />
              </div>
            )}
            {form.source_type === "file" && (
              <label className="flex items-center gap-2 self-end pb-2 text-sm">
                <Switch aria-label="Loop playback" checked={form.loop} onCheckedChange={(v) => set("loop", v)} />
                Loop playback
              </label>
            )}
          </div>

          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <label className="flex items-center gap-2 text-sm">
              <Switch aria-label="Enabled" checked={form.is_enabled} onCheckedChange={(v) => set("is_enabled", v)} />
              Enabled
            </label>
            {!isEdit && (
              <label className="flex items-center gap-2 text-sm">
                <Switch aria-label="Connect after saving" checked={form.connect} disabled={!form.is_enabled} onCheckedChange={(v) => set("connect", v)} />
                Connect after saving
              </label>
            )}
          </div>

          {test && (
            <Alert variant={test.ok ? "default" : "destructive"} role="status">
              {test.ok ? <CheckCircle2 className="text-status-online" /> : <XCircle />}
              <AlertTitle>{test.ok ? "Connection OK" : "Connection failed"}</AlertTitle>
              <AlertDescription>
                <p>{test.message}</p>
                {test.ok && (
                  <p className="font-mono text-xs">
                    {test.resolution ? `${test.resolution.width}×${test.resolution.height}` : "—"}
                    {test.nominal_fps ? ` · ${test.nominal_fps.toFixed(1)} fps advertised` : ""}
                    {test.latency_ms != null ? ` · ${Math.round(test.latency_ms)} ms` : ""}
                  </p>
                )}
                {!test.ok && test.failure_kind && <p className="text-xs">{FAILURE_HINT[test.failure_kind]}</p>}
              </AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </form>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button type="button" variant="outline" onClick={runTest} disabled={testing || !form.source_uri.trim()}>
            {testing ? <Loader2 className="animate-spin" /> : <PlugZap />}
            {testing ? "Testing…" : "Test connection"}
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" form="camera-form" disabled={saving || !form.name.trim() || !form.source_uri.trim()}>
              {saving && <Loader2 className="animate-spin" />}
              {isEdit ? "Save changes" : "Add camera"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
