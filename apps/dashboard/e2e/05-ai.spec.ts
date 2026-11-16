import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("AI control center: enable / disable, honest status, capabilities", async ({
  page,
}) => {
  await login(page);
  await nav(page, "AI Engine");
  await expect(page.getByText(/standard/i).first()).toBeVisible();
  const toggle = page.getByRole("switch", { name: "AI enabled" });
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
  // no model file in the test environment: the AI reports it instead of pretending to run
  await expect(
    page.getByText(/offline|error|recovering|loading/i).first(),
  ).toBeVisible({ timeout: 20_000 });
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByText("Disabled").first()).toBeVisible();
});
