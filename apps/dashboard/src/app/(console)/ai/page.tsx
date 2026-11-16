import type { Metadata } from "next";

import { AIView } from "./ai-view";

export const metadata: Metadata = { title: "AI Engine" };

export default function AIPage() {
  return <AIView />;
}
