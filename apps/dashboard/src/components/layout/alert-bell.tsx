"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { insights } from "@/lib/api/insights";
import { usePermissions } from "@/lib/auth/permissions";
import { useLiveMessages } from "@/lib/realtime/use-live-messages";

/** Top-bar bell: number of open alerts, refreshed live on `alert.created` (and every minute). */
export function AlertBell() {
  const { token, can } = usePermissions();
  const allowed = can("events:read");
  const [open, setOpen] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (!token || !allowed) return;
    const c = new AbortController();
    insights.alertCounts(token, c.signal).then(
      (r) => setOpen(r.open),
      () => {},
    );
    return () => c.abort();
  }, [token, allowed, tick]);

  useEffect(() => {
    const bump = () => setTick((n) => n + 1);
    const id = setInterval(bump, 60_000);
    window.addEventListener("aci:alerts-changed", bump);
    return () => (
      clearInterval(id),
      window.removeEventListener("aci:alerts-changed", bump)
    );
  }, []);

  useLiveMessages(["alert.created"], () => {
    setTick((n) => n + 1);
    setFlash(true);
    setTimeout(() => setFlash(false), 2500);
  });

  if (!allowed) return null;
  const label = open
    ? `${open} open alert${open === 1 ? "" : "s"}`
    : "No open alerts";
  return (
    <Button
      asChild
      size="icon"
      variant="ghost"
      className="relative"
      aria-label={label}
      title={label}
    >
      <Link href="/alerts">
        <Bell className={flash ? "animate-bounce" : ""} />
        {open ? (
          <span
            data-testid="alert-count"
            className="absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full bg-destructive px-1 font-mono text-[10px] leading-4 text-white"
          >
            {open > 99 ? "99+" : open}
          </span>
        ) : null}
      </Link>
    </Button>
  );
}
