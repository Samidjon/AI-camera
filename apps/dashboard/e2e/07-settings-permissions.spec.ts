import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("administrator manages users, roles and retention", async ({ page }) => {
  await login(page, "admin");
  await nav(page, "Settings");
  await page.getByRole("tab", { name: "Users" }).click();
  await expect(page.getByTestId("users-table")).toContainText(
    "viewer@e2e.local",
  );
  await page.getByRole("button", { name: "Add user" }).click();
  const dialog = page.getByRole("dialog", { name: "Add user" });
  await dialog.getByLabel("Email").fill("new.operator@e2e.local");
  await dialog.getByLabel("Password").fill("brand-new-password-1");
  await dialog.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByTestId("users-table")).toContainText(
    "new.operator@e2e.local",
  );

  await page.getByRole("tab", { name: "Roles & permissions" }).click();
  await expect(page.getByTestId("roles-matrix")).toContainText(
    "observations:read",
  );

  await page.getByRole("tab", { name: "Data & privacy" }).click();
  await page.getByLabel("Keep person observations (days)").fill("14");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Saved\. Retention runs hourly/)).toBeVisible();
});

test("viewer sees a read-only console", async ({ page }) => {
  await login(page, "viewer");
  const main = page.getByRole("navigation", { name: "Main" });
  await expect(main.getByRole("link", { name: "Observations" })).toHaveCount(0); // privacy: no permission
  await nav(page, "Cameras");
  await expect(page.getByText("E2E Gate").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Add camera" })).toHaveCount(0);
  await nav(page, "Devices");
  await expect(page.getByRole("button", { name: "Add device" })).toHaveCount(0);
  await nav(page, "Events");
  await expect(page.getByRole("button", { name: "Export CSV" })).toHaveCount(0);
  await nav(page, "Settings");
  await expect(page.getByRole("tab", { name: "Users" })).toHaveCount(0);
  await page.goto("/observations");
  await expect(page.getByText("No access")).toBeVisible();
});

test("operator sees observations but not administration", async ({ page }) => {
  await login(page, "operator");
  await nav(page, "Observations");
  await expect(page.getByText("No identification")).toBeVisible();
  await expect(page.getByText("No access")).toHaveCount(0);
  await nav(page, "Settings");
  await expect(page.getByRole("tab", { name: "Users" })).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "Roles & permissions" }),
  ).toHaveCount(0);
});
