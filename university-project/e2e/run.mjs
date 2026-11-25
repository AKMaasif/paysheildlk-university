// Starts a throwaway backend (empty database, no AI keys, simple face engine) and the production frontend,
// runs every spec in specs/, then stops both. Usage: node run.mjs [spec-file ...]
import { spawn, spawnSync } from "node:child_process";
import { closeSync, existsSync, mkdtempSync, openSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const API = process.env.E2E_API_PORT || "8000";
const WEB = process.env.E2E_WEB_PORT || "3000";
const work = mkdtempSync(path.join(tmpdir(), "payshield-e2e-"));
const children = [];

// Logs go to files, not pipes. This script waits on the test run without reading, and a full pipe would stall the server.
function start(name, cmd, args, cwd, env) {
  const file = path.join(work, `${name}.log`);
  const fd = openSync(file, "a");
  const child = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: ["ignore", fd, fd] });
  closeSync(fd);
  child.log = () => readFileSync(file, "utf8");
  child.name = name;
  children.push(child);
  return child;
}

async function waitFor(url, label, child) {
  for (let i = 0; i < 120; i++) {
    if (child.exitCode !== null) throw new Error(`${label} stopped early:\n${child.log().slice(-2000)}`);
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${label} did not start:\n${child.log().slice(-2000)}`);
}

function stop() {
  for (const c of children) c.kill("SIGTERM");
}
process.on("SIGINT", () => { stop(); process.exit(130); });

let code = 1;
try {
  if (!existsSync(path.join(root, "frontend/.next/BUILD_ID"))) {
    console.log("building the frontend once");
    const built = spawnSync("npm", ["run", "build"], { cwd: path.join(root, "frontend"), stdio: "inherit" });
    if (built.status !== 0) throw new Error("frontend build failed");
  }
  for (const [name, url] of [["backend", `http://localhost:${API}/api/health`], ["frontend", `http://localhost:${WEB}/c`]]) {
    const taken = await fetch(url).then(() => true, () => false);
    if (taken) throw new Error(`Something is already answering on the ${name} port. Stop it first so the tests start from an empty database.`);
  }
  const backend = start("backend", "python", ["-m", "uvicorn", "app.main:app", "--port", API], path.join(root, "backend"), {
    DATABASE_URL: `sqlite:///${path.join(work, "e2e.db")}`,
    CHECKPOINT_PATH: path.join(work, "checkpoints.sqlite"),
    FACE_ENGINE: "legacy",
    RATE_LIMIT_SCALE: "20",
    DEMO_ASSIST: "true",
    XAI_API_KEY: "",
    META_API_KEY: "",
    REDIS_URL: "",
  });
  await waitFor(`http://localhost:${API}/api/health`, "backend", backend);
  const web = start("frontend", "npm", ["run", "start", "--", "-p", WEB], path.join(root, "frontend"), {
    API_PROXY_URL: `http://localhost:${API}`,
    NEXT_PUBLIC_WS_PORT: API,
  });
  await waitFor(`http://localhost:${WEB}/c`, "frontend", web);

  const specs = process.argv.slice(2).length
    ? process.argv.slice(2)
    : readdirSync(path.join(here, "specs")).filter((f) => f.endsWith(".spec.mjs")).sort().map((f) => path.join("specs", f));
  const result = spawnSync("node", ["--test", "--test-concurrency=1", "--test-timeout=900000", ...specs], {
    cwd: here,
    stdio: "inherit",
    env: { ...process.env, E2E_API: `http://localhost:${API}`, E2E_WEB: `http://localhost:${WEB}` },
  });
  code = result.status ?? 1;
} catch (err) {
  console.error(String(err));
} finally {
  if (process.env.E2E_LOGS) {
    const { writeFileSync } = await import("node:fs");
    for (const c of children) writeFileSync(path.join(process.env.E2E_LOGS, `${c.name}.log`), c.log());
  }
  stop();
}
process.exit(code);
