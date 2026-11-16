import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("fleet events: filters, search, pagination, CSV export", async ({
  page,
}) => {
  await login(page);
  await nav(page, "Events");
  const rows = page.getByTestId("events-table").locator("tr[data-event-type]");
  // the moving-object video produces real motion events on the agent, uploaded to the server
  await expect
    .poll(
      async () => {
        await page.getByRole("button", { name: "Refresh events" }).click();
        return rows.count();
      },
      { timeout: 60_000, intervals: [3_000] },
    )
    .toBeGreaterThan(0);

  await page.getByTestId("category-motion").click();
  await expect
    .poll(async () => rows.count(), { timeout: 30_000 })
    .toBeGreaterThan(0);
  for (const type of await rows.evaluateAll((els) =>
    els.map((e) => e.getAttribute("data-event-type")),
  )) {
    expect([
      "motion_started",
      "motion_ended",
      "camera_motion_activity",
    ]).toContain(type);
  }

  await page.getByLabel("Search events").fill("E2E Yard");
  // wait for the debounced search to replace the rows (not just for any rows)
  await expect
    .poll(
      async () => {
        const cameras = await rows.locator("td:nth-child(3)").allInnerTexts();
        return cameras.length > 0 && cameras.every((c) => c === "E2E Yard");
      },
      { timeout: 20_000 },
    )
    .toBe(true);
  await page.getByLabel("Search events").fill("no-such-camera-xyz");
  await expect(page.getByText("No events match these filters.")).toBeVisible();
  await page.getByLabel("Search events").fill("");
  await page.getByTestId("category-motion").click();
  await expect.poll(async () => rows.count(), { timeout: 15_000 }).toBeGreaterThan(0);

  await expect(page.getByText(/^Page 1/)).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^events-.*\.csv$/);
  const csv = readFileSync((await file.path()) as string, "utf-8");
  expect(csv.split("\n")[0]).toContain(
    "occurred_at,event_type,source,device,camera",
  );
  expect(csv.trim().split("\n").length).toBeGreaterThan(1);

  // the local tab works independently of the server
  await page.getByRole("tab", { name: /this agent/i }).click();
  await expect(page.getByText(/stored on this edge agent/i)).toBeVisible();
});
