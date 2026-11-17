import type { Metadata } from "next";

import { HealthView } from "./health-view";

export const metadata: Metadata = { title: "System health" };

export default function HealthPage() {
  return <HealthView />;
}
