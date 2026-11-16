import type { Metadata } from "next";

import { ModulePending } from "@/components/common/module-pending";
import { PageHeader } from "@/components/common/page-header";

export const metadata: Metadata = { title: "Analytics" };

export default function AnalyticsPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Analytics" description="Activity statistics built from real events" />
      <ModulePending
        mvp="MVP 08"
        title="Analytics & Alerts"
        description="Analytics are computed only from stored events. Standard (motion) analytics and AI analytics are kept separate; motion counts are never presented as people counts."
        planned={[
          "Motion activity and frequency",
          "Zone activity and occupancy estimate",
          "Line-crossing counts",
          "People counting (AI mode only)",
          "Daily / weekly / monthly reports",
          "Configurable alerts and email",
        ]}
      />
    </div>
  );
}
