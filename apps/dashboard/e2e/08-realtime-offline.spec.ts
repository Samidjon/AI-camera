import { expect, test } from "@playwright/test";

import { login, nav, SUPERVISOR } from "./helpers";

test("server outage: stale state shown, no false Online, reconnects and refreshes", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await login(page);
  await nav(page, "Devices");
  const live = page.getByTestId("live-status");
  await expect(live).toHaveAttribute("data-state", "live");
  await expect(page.getByTestId("fleet-table")).toBeVisible();

  await fetch(`${SUPERVISOR}/stop`, { method: "POST" });
  await expect(live).not.toHaveAttribute("data-state", "live", {
    timeout: 20_000,
  });
  await expect(page.getByTestId("stale-banner")).toBeVisible({
    timeout: 30_000,
  });
  // fleet statuses turn "Unknown" instead of keeping the last "Online"
  await expect(page.getByTestId("fleet-table")).toContainText("Unknown", {
    timeout: 45_000,
  });
  await expect(page.getByText(/last known/i).first()).toBeVisible();

  await fetch(`${SUPERVISOR}/start`, { method: "POST" });
  await expect(live).toHaveAttribute("data-state", "live", { timeout: 90_000 });
  await expect(page.getByTestId("stale-banner")).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(page.getByTestId("fleet-table")).not.toContainText("Unknown", {
    timeout: 45_000,
  });
});
