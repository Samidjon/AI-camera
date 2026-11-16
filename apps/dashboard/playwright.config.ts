import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against the REAL stack: central server (FastAPI, fresh SQLite), edge agent
 * (generated moving-object video → real motion events) and a production build of this dashboard,
 * on separate ports so a developer's running instances are untouched.
 *
 *   npm run test:e2e            (needs uv, Python deps of both services, Microsoft Edge or Chrome)
 *   E2E_BROWSER_CHANNEL=chrome npm run test:e2e
 */
const channel = process.env.E2E_BROWSER_CHANNEL ?? "msedge";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1, // one shared backend; specs run in file order
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:13000",
    channel,
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      command: "node e2e/backend/supervisor.mjs",
      url: "http://127.0.0.1:18000/api/v1/health",
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command:
        "uv run --project ../../edge/camera-agent python e2e/backend/start_agent.py",
      url: "http://127.0.0.1:18100/api/v1/health",
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: "npx next build && npx next start --port 13000",
      url: "http://localhost:13000/login",
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        NEXT_DIST_DIR: ".next-e2e",
        NEXT_PUBLIC_API_URL: "http://localhost:18000",
        NEXT_PUBLIC_AGENT_URL: "http://localhost:18100",
      },
    },
  ],
});
