import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("enroll the edge agent, see it live, push a configuration", async ({
  page,
}) => {
  await login(page);
  await nav(page, "Devices");
  await expect(page.getByText("No edge devices yet")).toBeVisible();

  // enrollment token (shown once)
  await page.getByRole("button", { name: "Add device" }).click();
  await page.getByRole("button", { name: "Create token" }).click();
  const token = await page
    .getByRole("dialog")
    .getByLabel("Enrollment token")
    .inputValue();
  expect(token).toMatch(/^aci_enr_/);
  await page.getByRole("button", { name: "Done" }).click();

  // register the agent from its own tab (the agent itself talks to the server)
  await page.getByRole("tab", { name: /this agent/i }).click();
  await page
    .getByPlaceholder("https://server.example.com")
    .fill("http://127.0.0.1:18000");
  await page.getByRole("textbox", { name: "Enrollment token" }).fill(token);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(
    page.getByText("Registered with the central server."),
  ).toBeVisible({ timeout: 30_000 });

  // the fleet shows the device with live health
  await page.getByRole("tab", { name: /fleet/i }).click();
  const row = page.getByTestId("fleet-table").locator("tbody tr").first();
  await expect(row).toContainText(/online|synchronizing|degraded/i, {
    timeout: 30_000,
  });
  await expect(row).toContainText("2/2"); // cameras online / total
  await expect(row).toContainText("%"); // CPU / RAM / disk

  // device details + a configuration version applied and acknowledged by the agent
  await row.click();
  await expect(page.getByText("Cameras (2)")).toBeVisible();
  await page.getByPlaceholder("local").fill("21");
  await page.getByRole("button", { name: "Save as new version" }).click();
  await expect(page.getByText(/Version 1 queued/)).toBeVisible();
  await expect(page.getByText("Applied v1")).toBeVisible({ timeout: 30_000 });
});
