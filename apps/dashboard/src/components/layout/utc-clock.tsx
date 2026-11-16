"use client";

import { useEffect, useState } from "react";

/** Operator clock (local time + UTC offset). Rendered after mount to avoid hydration mismatch. */
export function UtcClock() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  if (!now) return <span className="hidden w-[132px] sm:block" aria-hidden />;

  const offsetMin = -now.getTimezoneOffset();
  const sign = offsetMin >= 0 ? "+" : "−";
  const offset = `UTC${sign}${String(Math.floor(Math.abs(offsetMin) / 60)).padStart(2, "0")}:${String(Math.abs(offsetMin) % 60).padStart(2, "0")}`;

  return (
    <time
      dateTime={now.toISOString()}
      className="tabular hidden items-baseline gap-1.5 font-mono text-xs text-muted-foreground sm:flex"
    >
      <span className="text-foreground">{now.toLocaleTimeString(undefined, { hour12: false })}</span>
      <span className="text-[10px]">{offset}</span>
    </time>
  );
}
