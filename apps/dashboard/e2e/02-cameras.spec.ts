import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("add, edit and delete a camera", async ({ page }) => {
  await login(page);
  await nav(page, "Cameras");
  await expect(page.getByText("E2E Gate").first()).toBeVisible();
  await page.getByRole("button", { name: "Add camera" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add camera" });
  await dialog.getByLabel("Name", { exact: true }).fill("E2E Temporary");
  await dialog.getByLabel("Location").fill("Loading dock");
  await dialog.locator("#cam-source").fill("rtsp://192.0.2.10:554/stream");
  await dialog.getByRole("switch", { name: "Connect after saving" }).click(); // documentation address, never reachable
  await dialog
    .getByRole("button", { name: /add camera|save/i })
    .last()
    .click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("E2E Temporary").first()).toBeVisible();

  // details → edit
  await page.getByRole("radio", { name: "List view" }).click();
  await page.getByText("E2E Temporary").first().click();
  await page.getByRole("button", { name: "Edit" }).click();
  const edit = page.getByRole("dialog", { name: "Edit camera" });
  await edit.getByLabel("Name", { exact: true }).fill("E2E Renamed");
  await edit.getByRole("button", { name: /save/i }).last().click();
  await expect(edit).toBeHidden();
  await expect(page.getByText("E2E Renamed").first()).toBeVisible();

  // delete (reopen the details: saving an edit closes them)
  await page.getByText("E2E Renamed").first().click();
  await page.getByRole("button", { name: "Delete" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Delete camera" })
    .click();
  await expect(page.getByText("E2E Renamed")).toHaveCount(0);
});
