import type { Metadata } from "next";

import { ModulePending } from "@/components/common/module-pending";
import { PageHeader } from "@/components/common/page-header";

export const metadata: Metadata = { title: "Events" };

export default function EventsPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Events" description="Motion, zone, line-crossing and system events" />
      <ModulePending
        mvp="MVP 03 / 06"
        title="Event Stream"
        description="The camera_events table (idempotent by event UUID) exists. Events are produced locally by the Standard Vision Engine (MVP 03) and synchronized to this server in MVP 06."
        planned={[
          "Motion started / ended",
          "Zone entry / exit",
          "Line crossing with direction",
          "Camera and device errors",
          "Date, camera and type filters",
          "CSV export",
        ]}
      />
    </div>
  );
}
