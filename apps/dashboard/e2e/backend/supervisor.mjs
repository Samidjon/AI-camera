// Runs the central server for Playwright and lets a test stop / start it (server outage test).
// Control: POST http://127.0.0.1:18001/stop | /start. The process tree is killed on Windows too.
import { spawn, spawnSync } from "node:child_process";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.resolve(here, "../../../api-server");
let child = null;

function start(fresh) {
  if (child) return;
  child = spawn(
    "uv",
    ["run", "--project", apiDir, "python", path.join(here, "start_api.py")],
    {
      env: { ...process.env, E2E_FRESH: fresh ? "1" : "0" },
      stdio: "inherit",
      shell: process.platform === "win32",
    },
  );
  child.on("exit", () => {
    child = null;
  });
}

function stop() {
  if (!child) return;
  if (process.platform === "win32")
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
  else child.kill("SIGTERM");
  child = null;
}

http
  .createServer((req, res) => {
    if (req.method === "POST" && req.url === "/stop") stop();
    else if (req.method === "POST" && req.url === "/start") start(false);
    res
      .writeHead(200, { "content-type": "application/json" })
      .end(JSON.stringify({ running: Boolean(child) }));
  })
  .listen(18001, "127.0.0.1");

start(true);
for (const sig of ["SIGINT", "SIGTERM", "exit"])
  process.on(sig, () => (stop(), sig !== "exit" && process.exit(0)));
