import { AppShell } from "@/components/layout/app-shell";

// Console pages are shown only after the client verifies the session (see next.config.ts
// instantInsights). Authorization itself is enforced by the API on every request.
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
