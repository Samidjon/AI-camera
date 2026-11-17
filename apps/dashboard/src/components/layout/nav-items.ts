import {
  Activity,
  BarChart3,
  BellRing,
  BrainCircuit,
  Cctv,
  Cpu,
  HeartPulse,
  LayoutDashboard,
  MonitorPlay,
  Settings,
  UserRoundSearch,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** MVP in which the module's backend arrives; undefined = available now. */
  availableIn?: string;
  /** Hide the entry for roles without this permission (the API enforces it anyway). */
  permission?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  {
    href: "/monitoring",
    label: "Live",
    icon: MonitorPlay,
    permission: "cameras:read",
  },
  {
    href: "/cameras",
    label: "Cameras",
    icon: Cctv,
    permission: "cameras:read",
  },
  { href: "/devices", label: "Devices", icon: Cpu, permission: "devices:read" },
  {
    href: "/events",
    label: "Events",
    icon: Activity,
    permission: "events:read",
  },
  {
    href: "/observations",
    label: "Observations",
    icon: UserRoundSearch,
    permission: "observations:read",
  },
  {
    href: "/ai",
    label: "AI Engine",
    icon: BrainCircuit,
    permission: "cameras:read",
  },
  {
    href: "/analytics",
    label: "Analytics",
    icon: BarChart3,
    permission: "events:read",
  },
  {
    href: "/alerts",
    label: "Alerts",
    icon: BellRing,
    permission: "events:read",
  },
  {
    href: "/monitoring-health",
    label: "System health",
    icon: HeartPulse,
    permission: "devices:read",
  },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/"
    ? pathname === "/"
    : pathname === href || pathname.startsWith(`${href}/`);
}
