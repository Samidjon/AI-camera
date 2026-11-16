import { expect, test } from "@playwright/test";

import { login, nav, PASSWORD, USERS } from "./helpers";

test("wrong password is rejected, correct one signs in", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(USERS.admin);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/invalid email or password/i)).toBeVisible();
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("console requires a session", async ({ page }) => {
  await page.goto("/cameras");
  await expect(page).toHaveURL(/\/login/);
});

test("navigation reaches every module", async ({ page }) => {
  await login(page, "admin");
  const pages: [string, string][] = [
    ["Live", "Live monitoring"],
    ["Cameras", "Cameras"],
    ["Devices", "Devices"],
    ["Events", "Events"],
    ["Observations", "Anonymous observations"],
    ["AI Engine", "AI Engine"],
    ["Settings", "Settings"],
    ["Overview", "Overview"],
  ];
  for (const [link, heading] of pages) {
    await nav(page, link);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
  }
  await expect(page.getByTestId("overview-counters")).toContainText(
    "Cameras online",
  );
});

test("theme switch persists", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "Theme" }).click();
  await page.getByRole("menuitemradio", { name: "Light" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.getByRole("button", { name: "Theme" }).click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
});
