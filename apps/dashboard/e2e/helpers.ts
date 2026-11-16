import { expect, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:18000/api/v1";
export const AGENT = "http://127.0.0.1:18100/api/v1";
export const SUPERVISOR = "http://127.0.0.1:18001";
export const PASSWORD = "e2e-password-123456";
export const USERS = {
  admin: "admin@e2e.local",
  operator: "operator@e2e.local",
  viewer: "viewer@e2e.local",
} as const;

export async function login(page: Page, who: keyof typeof USERS = "admin") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(USERS[who]);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

export async function apiToken(
  who: keyof typeof USERS = "admin",
): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: USERS[who], password: PASSWORD }),
  });
  return (await r.json()).access_token;
}

export async function nav(page: Page, label: string) {
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: label, exact: true })
    .click();
}
