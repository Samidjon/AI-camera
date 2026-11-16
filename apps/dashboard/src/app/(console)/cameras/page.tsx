import type { Metadata } from "next";

import { ModulePending } from "@/components/common/module-pending";
import { PageHeader } from "@/components/common/page-header";

export const metadata: Metadata = { title: "Cameras" };

export default function CamerasPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Cameras" description="Camera inventory, live preview and connection state" />
      <ModulePending
        mvp="MVP 02"
        title="Camera Manager"
        description="The database schema for cameras and camera groups exists (MVP 01). Connecting real cameras, stream workers and live preview are built in MVP 02."
        planned={[
          "RTSP, ONVIF, USB and video-file sources",
          "Add / edit / delete cameras",
          "Live preview with FPS and resolution",
          "Automatic reconnection per camera",
          "Encrypted storage for connection secrets",
          "Camera groups and locations",
        ]}
      />
    </div>
  );
}
