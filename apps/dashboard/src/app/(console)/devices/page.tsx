import type { Metadata } from "next";

import { ModulePending } from "@/components/common/module-pending";
import { PageHeader } from "@/components/common/page-header";

export const metadata: Metadata = { title: "Devices" };

export default function DevicesPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Devices" description="Edge agents running on Linux mini PCs and NVIDIA Jetson" />
      <ModulePending
        mvp="MVP 05 / 06"
        title="Edge Device Management"
        description="Edge agents work autonomously without this server. Device registration, heartbeats and remote configuration arrive with the Edge Runtime (MVP 05) and Central Server (MVP 06)."
        planned={[
          "Device registration and authentication",
          "Heartbeat and agent status",
          "CPU, RAM, GPU, temperature, disk",
          "AI status per device",
          "Versioned remote configuration",
          "Device revocation",
        ]}
      />
    </div>
  );
}
