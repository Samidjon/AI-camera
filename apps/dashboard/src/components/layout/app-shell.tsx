"use client";

import { Loader2, Menu, ServerOff } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { API_BASE_URL } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import { SystemStatusProvider } from "@/lib/system/system-status-provider";

import { Brand } from "./brand";
import { isActive, NAV_ITEMS } from "./nav-items";
import { ServerLinkStatus } from "./server-link-status";
import { SidebarNav } from "./sidebar-nav";
import { UserMenu } from "./user-menu";
import { UtcClock } from "./utc-clock";

function FullScreenMessage({ children }: { children: React.ReactNode }) {
  return <div className="grid min-h-dvh place-items-center bg-background p-6">{children}</div>;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { state, retry } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (state.status === "anonymous") router.replace("/login");
  }, [state.status, router]);

  const ready = state.status === "authenticated";

  let gate: React.ReactNode = null;
  if (state.status === "loading" || state.status === "anonymous") {
    gate = (
      <FullScreenMessage>
        <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Verifying session…
        </div>
      </FullScreenMessage>
    );
  } else if (state.status === "server_unreachable") {
    gate = (
      <FullScreenMessage>
        <div className="max-w-md rounded-lg border bg-card p-6 text-center" role="alert">
          <ServerOff className="mx-auto size-6 text-status-error" aria-hidden />
          <h1 className="mt-3 text-base font-semibold">Central server unreachable</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            The dashboard cannot verify your session at <span className="font-mono text-foreground">{API_BASE_URL}</span>.
            Edge agents keep recording locally while the server is offline.
          </p>
          <p className="mt-2 font-mono text-xs text-muted-foreground">{state.message}</p>
          <Button className="mt-4" onClick={retry}>
            Retry connection
          </Button>
        </div>
      </FullScreenMessage>
    );
  }

  if (!ready) return gate;

  return (
    <SystemStatusProvider>
      <div className="flex min-h-dvh bg-background">
        <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r bg-sidebar lg:flex">
          <div className="flex h-14 items-center border-b px-4">
            <Brand />
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            <SidebarNav />
          </div>
          <div className="border-t px-4 py-3 font-mono text-[10px] text-muted-foreground">
            Offline-first · Edge agents run without this server
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="mx-auto w-full max-w-[1600px] flex-1 p-4 md:p-6">{children}</main>
        </div>
      </div>
    </SystemStatusProvider>
  );
}

function TopBar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const current = NAV_ITEMS.find((i) => isActive(pathname, i.href));

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur md:px-6">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="Open navigation">
            <Menu />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Main sections of the control center</SheetDescription>
          <div className="flex h-14 items-center border-b px-4">
            <Brand />
          </div>
          <div className="p-3">
            <SidebarNav onNavigate={() => setOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>
      <div className="lg:hidden">
        <Brand compact />
      </div>
      <div className="hidden min-w-0 items-center gap-2 text-sm lg:flex">
        <span className="text-muted-foreground">Control Center</span>
        <span className="text-muted-foreground/50">/</span>
        <span className="font-medium">{current?.label ?? ""}</span>
      </div>
      <div className="ml-auto flex items-center gap-3">
        <UtcClock />
        <ServerLinkStatus />
        <UserMenu />
      </div>
    </header>
  );
}
