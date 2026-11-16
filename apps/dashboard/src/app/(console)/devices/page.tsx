import type { Metadata } from "next";

import { DevicesTabs } from "./devices-tabs";

export const metadata: Metadata = { title: "Devices" };

export default function DevicesPage() {
  return <DevicesTabs />;
}
