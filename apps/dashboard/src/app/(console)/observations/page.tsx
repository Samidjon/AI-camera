import type { Metadata } from "next";

import { ObservationsView } from "./observations-view";

export const metadata: Metadata = { title: "Observations" };

export default function ObservationsPage() {
  return <ObservationsView />;
}
