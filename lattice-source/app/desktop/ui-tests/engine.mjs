// Starts the engine for the UI tests: demo model (no language model needed), a fresh data folder,
// listening on the port the test UI talks to. Playwright starts and stops it (see playwright.config.ts).
import { spawn } from "node:child_process";
import { rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = join(here, "..", "..", "backend");
const dataDir = join(here, ".engine-data");
rmSync(dataDir, { recursive: true, force: true });

const python = join(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const engine = spawn(python, ["-m", "oasis_engine"], {
  cwd: backend,
  stdio: "inherit",
  env: {
    ...process.env,
    OASIS_LLM_MODEL: "demo",
    OASIS_DATA_DIR: dataDir,
    OASIS_PORT: process.env.UI_TEST_ENGINE_PORT ?? "8799",
    OASIS_CORS_ORIGINS: `http://localhost:${process.env.UI_TEST_UI_PORT ?? "1431"}`,
    OASIS_LOG_LEVEL: "warning",
    // the engine exits when this script goes (on Windows the venv python.exe is a launcher, so killing
    // it alone would leave the real engine running and holding the port)
    OASIS_PARENT_PID: String(process.pid),
  },
});
const stop = () => engine.kill();
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);
engine.on("exit", (code) => process.exit(code ?? 0));
