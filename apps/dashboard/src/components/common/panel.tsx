import { cn } from "@/lib/utils";

/** Bordered console panel with a compact uppercase header — the base surface of the dashboard. */
export function Panel({
  title,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: string;
  icon?: React.ComponentType<{ className?: string }>;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("rounded-lg border bg-card text-card-foreground", className)}>
      {(title || actions) && (
        <header className="flex h-10 items-center justify-between gap-2 border-b px-4">
          <h2 className="flex items-center gap-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
            {Icon && <Icon className="size-3.5" />}
            {title}
          </h2>
          {actions && <div className="flex items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}
