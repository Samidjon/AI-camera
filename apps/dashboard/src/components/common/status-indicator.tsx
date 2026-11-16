import { cn } from "@/lib/utils";

export type StatusTone = "online" | "warning" | "error" | "idle";

const toneClass: Record<StatusTone, string> = {
  online: "bg-status-online",
  warning: "bg-status-warning",
  error: "bg-status-error",
  idle: "bg-status-idle",
};

const toneText: Record<StatusTone, string> = {
  online: "text-status-online",
  warning: "text-status-warning",
  error: "text-status-error",
  idle: "text-muted-foreground",
};

/** Status dot + label. The label is always rendered, so state is never conveyed by color alone. */
export function StatusIndicator({
  tone,
  label,
  pulse = false,
  className,
}: {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", toneText[tone], className)}>
      <span className="relative flex size-2" aria-hidden>
        {pulse && (
          <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", toneClass[tone])} />
        )}
        <span className={cn("relative inline-flex size-2 rounded-full", toneClass[tone])} />
      </span>
      {label}
    </span>
  );
}

export function componentTone(state: string | undefined): StatusTone {
  switch (state) {
    case "ok":
      return "online";
    case "degraded":
      return "warning";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

export function componentLabel(state: string | undefined): string {
  switch (state) {
    case "ok":
      return "Operational";
    case "degraded":
      return "Degraded";
    case "error":
      return "Error";
    case "not_configured":
      return "Not configured";
    default:
      return "Unknown";
  }
}
