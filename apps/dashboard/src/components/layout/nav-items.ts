import { Activity, BarChart3, Cctv, Cpu, LayoutDashboard, Settings } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** MVP in which the module's backend arrives; undefined = available now. */
  availableIn?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/cameras", label: "Cameras", icon: Cctv },
  { href: "/devices", label: "Devices", icon: Cpu, availableIn: "MVP 05" },
  { href: "/events", label: "Events", icon: Activity, availableIn: "MVP 03" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, availableIn: "MVP 08" },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
