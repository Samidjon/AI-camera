import { expect, test } from "@playwright/test";

import { login, nav } from "./helpers";

test("live wall streams frames over one WebSocket with overlays and grid sizes", async ({
  page,
}) => {
  const sockets: string[] = [];
  page.on("websocket", (ws) => sockets.push(ws.url()));
  await login(page);
  await nav(page, "Live");
  await expect(page.getByText("Stream live")).toBeVisible();
  const tiles = page.getByTestId("live-tile");
  await expect(tiles).toHaveCount(2);
  // frames are painted onto the canvases (data-frames counts painted frames)
  for (const canvas of await page
    .locator('[data-testid="live-tile"] canvas')
    .all()) {
    await expect
      .poll(
        async () => Number((await canvas.getAttribute("data-frames")) ?? 0),
        { timeout: 20_000 },
      )
      .toBeGreaterThan(3);
  }
  await expect(tiles.first().getByTestId("tile-fps")).toContainText(
    /\d+ \/ \d+(\.\d)? fps/,
  );
  // one multiplexed stream to the agent — no per-camera HTTP streams, no REST frame polling
  expect(sockets.filter((u) => u.includes(":18100/api/v1/live"))).toHaveLength(
    1,
  );

  // overlays
  const tracking = page.getByRole("button", { name: "Tracking" });
  await tracking.click();
  await expect(tracking).toHaveAttribute("aria-pressed", "true");

  // grid sizes, single view, fullscreen control
  for (const [size, label] of [
    ["1", "1 camera"],
    ["9", "9 cameras"],
    ["16", "16 cameras"],
    ["4", "4 cameras"],
  ]) {
    await page.getByRole("radio", { name: label }).click();
    await expect(page.getByTestId("live-wall")).toHaveAttribute(
      "data-grid",
      size,
    );
  }
  await tiles.first().hover();
  await page
    .getByRole("button", { name: /single view/i })
    .first()
    .click();
  await expect(page.getByTestId("live-wall")).toHaveAttribute("data-grid", "1");
  await expect(
    page.getByRole("button", { name: "Fullscreen", exact: true }),
  ).toBeEnabled();
});
