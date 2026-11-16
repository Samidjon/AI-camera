"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { usePermissions } from "@/lib/auth/permissions";
import { cn } from "@/lib/utils";

import { isActive, NAV_ITEMS } from "./nav-items";

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { can } = usePermissions();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV_ITEMS.filter((i) => !i.permission || can(i.permission)).map(
        ({ href, label, icon: Icon, availableIn }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "group relative flex h-9 items-center gap-3 rounded-md px-3 text-sm transition-colors outline-none",
                "focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                active
                  ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              {active && (
                <span
                  className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-sidebar-primary"
                  aria-hidden
                />
              )}
              <Icon
                className={cn(
                  "size-4",
                  active ? "text-sidebar-primary" : "opacity-80",
                )}
              />
              <span className="flex-1">{label}</span>
              {availableIn && (
                <span
                  className="font-mono text-[9px] tracking-wide text-muted-foreground/70"
                  title={`Planned for ${availableIn}`}
                >
                  {availableIn.replace("MVP ", "M")}
                </span>
              )}
            </Link>
          );
        },
      )}
    </nav>
  );
}
