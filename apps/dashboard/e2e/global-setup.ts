import { readFileSync } from "node:fs";
import path from "node:path";

import { AGENT, apiToken } from "./helpers";

/** Two cameras on the agent: the generated video, looped (real frames, real motion). */
export default async function globalSetup() {
  const token = await apiToken("admin");
  const video = readFileSync(
    path.join(__dirname, ".e2e-data", "agent", "video-path.txt"),
    "utf-8",
  ).trim();
  for (const name of ["E2E Gate", "E2E Yard"]) {
    const r = await fetch(`${AGENT}/cameras`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        name,
        source_type: "file",
        source_uri: video,
        options: { loop: true },
        connect: true,
      }),
    });
    if (!r.ok)
      throw new Error(`camera setup failed: ${r.status} ${await r.text()}`);
  }
}
