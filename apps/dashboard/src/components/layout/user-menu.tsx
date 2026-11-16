"use client";

import { LogOut, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth/auth-provider";

export function UserMenu() {
  const { state, logout } = useAuth();
  const router = useRouter();
  const user = state.status === "authenticated" ? state.user : null;
  const initials = (user?.full_name || user?.email || "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="gap-2 px-1.5"
          aria-label="Account menu"
        >
          <span className="grid size-6 place-items-center rounded-full bg-secondary font-mono text-[10px] font-semibold">
            {user ? initials : <UserRound className="size-3.5" />}
          </span>
          <span className="hidden max-w-[160px] truncate text-xs md:inline">
            {user?.email ?? "Offline session"}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="truncate text-sm">
            {user?.full_name || user?.email || "Unknown user"}
          </span>
          {user && (
            <span className="font-mono text-[10px] text-muted-foreground uppercase">
              {user.role.name}
            </span>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            logout();
            router.replace("/login");
          }}
        >
          <LogOut className="size-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
