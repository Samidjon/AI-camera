"use client";

import { HardDrive, Network } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { EventsView } from "./events-view";
import { FleetEventsView } from "./fleet-events-view";

export function EventsTabs() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Events"
        description="Motion (Standard Engine), person detection (AI), zones, line crossings and camera / AI / device errors. Motion and person events are never mixed."
      />
      <Tabs defaultValue="fleet" className="gap-4">
        <TabsList>
          <TabsTrigger value="fleet">
            <Network className="size-4" /> All devices (central server)
          </TabsTrigger>
          <TabsTrigger value="local">
            <HardDrive className="size-4" /> This agent (local)
          </TabsTrigger>
        </TabsList>
        <TabsContent value="fleet">
          <FleetEventsView />
        </TabsContent>
        <TabsContent value="local">
          <EventsView embedded />
        </TabsContent>
      </Tabs>
    </div>
  );
}
