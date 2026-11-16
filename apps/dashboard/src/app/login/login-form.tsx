"use client";

import { ChevronDown, Loader2, LogIn, Server } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/layout/brand";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AGENT_BASE_URL } from "@/lib/agent/client";
import { API_BASE_URL, ApiError, NetworkError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import {
  hasCustomConnection,
  mixedContentProblem,
  resetConnection,
  saveConnection,
} from "@/lib/connection";

function messageFor(err: unknown): string {
  if (err instanceof NetworkError)
    return `Cannot reach the central server at ${API_BASE_URL}. Check the server address under “Connection”.`;
  if (err instanceof ApiError) {
    if (err.status === 401) return "Invalid email or password.";
    if (err.code === "database_not_configured")
      return "The server has no database configured (ACI_DATABASE_URL).";
    if (err.status === 422) return "Enter a valid email and password.";
    return err.message;
  }
  return "Unexpected error. Try again.";
}

function ConnectionSettings({ initiallyOpen }: { initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [api, setApi] = useState(API_BASE_URL);
  const [agentUrl, setAgentUrl] = useState(AGENT_BASE_URL);
  const [error, setError] = useState<string | null>(null);

  function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      if (mixedContentProblem(api.includes("://") ? api : `https://${api}`))
        throw new Error(
          "This page uses HTTPS, so the server must be reachable over HTTPS too.",
        );
      saveConnection(api, agentUrl);
      window.location.reload(); // every client re-reads the addresses
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid address");
    }
  }

  return (
    <div className="mt-4 rounded-lg border bg-card">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <Server className="size-4 text-muted-foreground" aria-hidden />
        <span className="flex-1">Connection</span>
        <span
          className="truncate font-mono text-[10px] text-muted-foreground"
          suppressHydrationWarning
        >
          {API_BASE_URL}
        </span>
        <ChevronDown
          className={`size-4 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {open && (
        <form
          onSubmit={save}
          className="flex flex-col gap-3 border-t px-4 py-4"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="api-url">Central server address</Label>
            <Input
              id="api-url"
              inputMode="url"
              autoCapitalize="none"
              placeholder="https://your-server.example.com"
              value={api}
              onChange={(e) => setApi(e.target.value)}
              suppressHydrationWarning
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="agent-url">
              Edge agent address (live video, cameras)
            </Label>
            <Input
              id="agent-url"
              inputMode="url"
              autoCapitalize="none"
              placeholder="https://your-agent.example.com (optional)"
              value={agentUrl}
              onChange={(e) => setAgentUrl(e.target.value)}
              suppressHydrationWarning
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="flex gap-2">
            <Button type="submit" size="sm">
              Save and reconnect
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                resetConnection();
                window.location.reload();
              }}
            >
              Reset to default
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Saved in this browser only. Ask your administrator for the addresses
            of your installation.
          </p>
        </form>
      )}
    </div>
  );
}

export function LoginForm() {
  const { state, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsConnection, setNeedsConnection] = useState(false);

  useEffect(() => {
    if (state.status === "authenticated") router.replace("/");
  }, [state.status, router]);

  useEffect(() => {
    // On a deployed site the build default (localhost) is useless: ask for the server first.
    const onLocalhost = ["localhost", "127.0.0.1"].includes(
      window.location.hostname,
    );
    if (
      !hasCustomConnection() &&
      !onLocalhost &&
      API_BASE_URL.includes("localhost")
    ) {
      void Promise.resolve().then(() => setNeedsConnection(true));
    }
  }, []);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(messageFor(err));
      if (err instanceof NetworkError) setNeedsConnection(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      <div className="mb-8 flex justify-center">
        <Brand />
      </div>
      <div className="rounded-lg border bg-card p-6 shadow-2xl shadow-black/30">
        <h1 className="text-base font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Operator access to the control center
        </p>

        <form
          onSubmit={onSubmit}
          className="mt-6 flex flex-col gap-4"
          noValidate
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(error) || undefined}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={Boolean(error) || undefined}
            />
          </div>

          {error && (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <Button
            type="submit"
            size="lg"
            disabled={submitting || !email || !password}
          >
            {submitting ? <Loader2 className="animate-spin" /> : <LogIn />}
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
      <ConnectionSettings
        key={String(needsConnection)}
        initiallyOpen={needsConnection}
      />
    </div>
  );
}
