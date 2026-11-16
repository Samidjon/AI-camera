import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { API, apiToken, login, nav } from "./helpers";

test("analytics: real motion statistics, no fake people numbers, CSV + PDF reports", async ({
  page,
}) => {
  await login(page);
  await nav(page, "Analytics");
  await expect(
    page.getByRole("heading", { name: "Analytics", exact: true }),
  ).toBeVisible();
  const kpis = page.getByTestId("standard-kpis");
  // the moving-object video produces real motion events → the count becomes > 0
  await expect
    .poll(
      async () => {
        await page.getByRole("button", { name: "Refresh" }).click();
        const text = (await kpis.innerText()).split("\n");
        return Number(
          text[text.indexOf("MOTION EVENTS") + 1]?.replace(/\D/g, "") || 0,
        );
      },
      { timeout: 60_000, intervals: [3_000] },
    )
    .toBeGreaterThan(0);
  // charts come with a table view
  await page.getByRole("button", { name: "Show table" }).first().click();
  await expect(
    page.getByRole("columnheader", { name: "Motion events" }).first(),
  ).toBeVisible();

  // no AI in the e2e stack → people analytics are explicitly unavailable, never zeros
  await expect(page.getByTestId("ai-unavailable")).toBeVisible();

  const csv = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download Daily report as CSV" })
    .click();
  const file = await csv;
  expect(file.suggestedFilename()).toMatch(/^daily-report-\d{8}\.csv$/);
  expect(readFileSync(await file.path(), "utf-8")).toContain("motion");

  const pdf = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download Weekly report as PDF" })
    .click();
  const pdfFile = await pdf;
  expect(
    readFileSync(await pdfFile.path())
      .subarray(0, 5)
      .toString(),
  ).toBe("%PDF-");
});

test("alerts: rule created in the UI fires on real events, live bell, acknowledge", async ({
  page,
}) => {
  await login(page);
  await nav(page, "Alerts");
  await page.getByRole("tab", { name: "Rules" }).click();
  await page.getByRole("button", { name: "New rule" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Condition").click();
  await page.getByRole("option", { name: "Motion · Motion detected" }).click();
  await dialog.getByLabel("Name").fill("E2E motion");
  await dialog.getByRole("button", { name: "Save rule" }).click();
  await expect(page.getByTestId("alert-rules")).toContainText("E2E motion");

  // rules validation: e-mail channel without recipients is refused by the server
  await page.getByRole("button", { name: "New rule" }).click();
  await page.getByRole("dialog").getByLabel("Also send e-mail").click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Save rule" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("recipient");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Cancel" })
    .click();

  // the next real motion event raises an alert; the bell updates live
  await page.getByRole("tab", { name: "Alerts" }).click();
  const alerts = page.getByTestId("alerts");
  await expect(alerts).toContainText("Motion detected", { timeout: 60_000 });
  await expect(page.getByTestId("alert-count")).toBeVisible();

  await alerts.getByRole("button", { name: "Acknowledge" }).first().click();
  await expect(alerts.getByText("acknowledged").first()).toBeVisible();

  // server side: viewer can read but not acknowledge
  const viewer = await apiToken("viewer");
  const list = await fetch(`${API}/alerts`, {
    headers: { Authorization: `Bearer ${viewer}` },
  });
  expect(list.status).toBe(200);
  const [first] = await list.json();
  const ack = await fetch(`${API}/alerts/${first.id}/acknowledge`, {
    method: "POST",
    headers: { Authorization: `Bearer ${viewer}` },
  });
  expect(ack.status).toBe(403);
});
