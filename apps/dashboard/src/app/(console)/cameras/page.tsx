import type { Metadata } from "next";

import { CamerasView } from "./cameras-view";

export const metadata: Metadata = { title: "Cameras" };

export default function CamerasPage() {
  return <CamerasView />;
}
