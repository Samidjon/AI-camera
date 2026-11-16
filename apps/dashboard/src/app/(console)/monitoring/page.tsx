import type { Metadata } from "next";

import { MonitoringView } from "./monitoring-view";

export const metadata: Metadata = { title: "Live monitoring" };

export default function MonitoringPage() {
  return <MonitoringView />;
}
