import { cn } from "@/lib/utils";

/** Compact KPI tile. `value` of null renders an explicit "—" (no data), never a fake number. */
export function MetricTile({
  label,
  value,
  hint,
  icon: Icon,
  className,
}: {
  label: string;
  value: React.ReactNode | null;
  hint?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-2 rounded-lg border bg-card px-4 py-3", className)}>
      <div className="flex items-center justify-between text-[11px] font-medium tracking-[0.06em] text-muted-foreground uppercase">
        <span>{label}</span>
        {Icon && <Icon className="size-3.5" />}
      </div>
      <div className="tabular font-mono text-xl leading-none font-semibold">{value ?? "—"}</div>
      {hint && <div className="truncate text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}
