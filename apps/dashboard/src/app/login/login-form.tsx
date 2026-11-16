"use client";

import { Loader2, LogIn } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/layout/brand";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_BASE_URL, ApiError, NetworkError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";

function messageFor(err: unknown): string {
  if (err instanceof NetworkError) return `Cannot reach the central server at ${API_BASE_URL}.`;
  if (err instanceof ApiError) {
    if (err.status === 401) return "Invalid email or password.";
    if (err.code === "database_not_configured") return "The server has no database configured (ACI_DATABASE_URL).";
    if (err.status === 422) return "Enter a valid email and password.";
    return err.message;
  }
  return "Unexpected error. Try again.";
}

export function LoginForm() {
  const { state, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state.status === "authenticated") router.replace("/");
  }, [state.status, router]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(messageFor(err));
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
        <p className="mt-1 text-sm text-muted-foreground">Operator access to the control center</p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4" noValidate>
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

          <Button type="submit" size="lg" disabled={submitting || !email || !password}>
            {submitting ? <Loader2 className="animate-spin" /> : <LogIn />}
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
      <p className="mt-6 text-center font-mono text-[10px] text-muted-foreground">{API_BASE_URL}</p>
    </div>
  );
}
