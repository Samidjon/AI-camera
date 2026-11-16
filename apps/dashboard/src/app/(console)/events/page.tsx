import type { Metadata } from "next";

import { EventsTabs } from "./events-tabs";

export const metadata: Metadata = { title: "Events" };

export default function EventsPage() {
  return <EventsTabs />;
}
