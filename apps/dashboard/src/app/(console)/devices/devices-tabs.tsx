"use client";

import { Cpu, Network } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { FleetView } from "@/components/fleet/fleet-view";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { DevicesView } from "./devices-view";

export function DevicesTabs() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Devices"
        description="Edge agents registered with the central server, and the agent this dashboard is connected to. Agents keep working when the server is down."
      />
      <Tabs defaultValue="fleet" className="gap-5">
        <TabsList>
          <TabsTrigger value="fleet">
            <Network className="size-4" /> Fleet (central server)
          </TabsTrigger>
          <TabsTrigger value="agent">
            <Cpu className="size-4" /> This agent
          </TabsTrigger>
        </TabsList>
        <TabsContent value="fleet">
          <FleetView />
        </TabsContent>
        <TabsContent value="agent">
          <DevicesView />
        </TabsContent>
      </Tabs>
    </div>
  );
}
