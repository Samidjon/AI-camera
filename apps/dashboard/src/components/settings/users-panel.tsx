"use client";

import { KeyRound, Loader2, Plus, Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Panel } from "@/components/common/panel";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { platform, type UserItem } from "@/lib/api/platform";
import { useAuth } from "@/lib/auth/auth-provider";
import { formatDateTime } from "@/lib/format";

const ROLES = ["administrator", "operator", "viewer"];

function errText(e: unknown) {
  return e instanceof ApiError ? e.message : "Central server is unreachable.";
}

export function UsersPanel({ token }: { token: string }) {
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.user.id : null;
  const [users, setUsers] = useState<UserItem[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<UserItem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [form, setForm] = useState({
    email: "",
    full_name: "",
    password: "",
    role: "viewer",
  });
  const [newPassword, setNewPassword] = useState("");

  const load = useCallback(async () => {
    try {
      setUsers(await platform.users(token));
    } catch (e) {
      setMessage({ ok: false, text: errText(e) });
    }
  }, [token]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  async function act(key: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(key);
    setMessage(null);
    try {
      await fn();
      setMessage({ ok: true, text: ok });
      await load();
      return true;
    } catch (e) {
      setMessage({ ok: false, text: errText(e) });
      return false;
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel
      title="Users"
      icon={Users}
      bodyClassName="p-0"
      actions={
        <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
          <Plus /> Add user
        </Button>
      }
    >
      {message && (
        <Alert
          variant={message.ok ? "default" : "destructive"}
          className="m-3 w-auto"
        >
          <AlertDescription>{message.text}</AlertDescription>
        </Alert>
      )}
      <Table data-testid="users-table">
        <TableHeader>
          <TableRow>
            <TableHead>User</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Active</TableHead>
            <TableHead>Last sign-in</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.map((u) => (
            <TableRow key={u.id} data-user={u.email}>
              <TableCell>
                <div className="font-medium">{u.full_name ?? u.email}</div>
                <div className="text-xs text-muted-foreground">{u.email}</div>
              </TableCell>
              <TableCell>
                <Select
                  value={u.role}
                  disabled={busy !== null || u.id === me}
                  onValueChange={(role) =>
                    void act(
                      `role-${u.id}`,
                      () => platform.updateUser(token, u.id, { role }),
                      `Role of ${u.email} changed to ${role}. It applies at their next sign-in on edge agents.`,
                    )
                  }
                >
                  <SelectTrigger
                    size="sm"
                    className="w-36"
                    aria-label={`Role of ${u.email}`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </TableCell>
              <TableCell>
                <Switch
                  checked={u.is_active}
                  disabled={busy !== null || u.id === me}
                  aria-label={`${u.email} active`}
                  onCheckedChange={(v) =>
                    void act(
                      `active-${u.id}`,
                      () => platform.updateUser(token, u.id, { is_active: v }),
                      v ? "Account enabled." : "Account disabled.",
                    )
                  }
                />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatDateTime(u.last_login_at)}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => (setResetFor(u), setNewPassword(""))}
                  aria-label={`Reset password of ${u.email}`}
                >
                  <KeyRound />
                </Button>
                {u.id !== me &&
                  (confirmDelete === u.id ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy !== null}
                      onClick={() =>
                        void act(
                          `del-${u.id}`,
                          () => platform.deleteUser(token, u.id),
                          `${u.email} deleted.`,
                        ).then(() => setConfirmDelete(null))
                      }
                    >
                      Confirm delete
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirmDelete(u.id)}
                      aria-label={`Delete ${u.email}`}
                    >
                      <Trash2 />
                    </Button>
                  ))}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add user</DialogTitle>
            <DialogDescription>
              The user signs in with this email and password. Passwords need at
              least 10 characters.
            </DialogDescription>
          </DialogHeader>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void act(
                "add",
                () =>
                  platform.createUser(token, {
                    ...form,
                    full_name: form.full_name || undefined,
                  }),
                `User ${form.email} created.`,
              ).then((ok) => {
                if (ok) {
                  setAdding(false);
                  setForm({
                    email: "",
                    full_name: "",
                    password: "",
                    role: "viewer",
                  });
                }
              });
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="nu-email">Email</Label>
              <Input
                id="nu-email"
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="nu-name">Name</Label>
              <Input
                id="nu-name"
                value={form.full_name}
                onChange={(e) =>
                  setForm({ ...form, full_name: e.target.value })
                }
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="nu-pass">Password</Label>
              <Input
                id="nu-pass"
                type="password"
                autoComplete="new-password"
                minLength={10}
                required
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Role</Label>
              <Select
                value={form.role}
                onValueChange={(role) => setForm({ ...form, role })}
              >
                <SelectTrigger aria-label="New user role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={busy !== null}>
                {busy === "add" && <Loader2 className="animate-spin" />} Create
                user
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={resetFor !== null}
        onOpenChange={(o) => !o && setResetFor(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset password</DialogTitle>
            <DialogDescription>
              {resetFor?.email} — at least 10 characters.
            </DialogDescription>
          </DialogHeader>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!resetFor) return;
              void act(
                "reset",
                () =>
                  platform.updateUser(token, resetFor.id, {
                    password: newPassword,
                  }),
                "Password changed.",
              ).then((ok) => ok && setResetFor(null));
            }}
          >
            <Input
              type="password"
              autoComplete="new-password"
              minLength={10}
              required
              aria-label="New password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
            <DialogFooter>
              <Button type="submit" disabled={busy !== null}>
                Set password
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}
