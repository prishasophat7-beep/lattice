// Stops the test engine and Vite before Playwright tries to. On Windows Playwright stops its web servers
// with `taskkill /T`, which is not always allowed (locked-down machines, sandboxes): the servers then
// outlive the run and Playwright waits 10 minutes for them. Stopping whatever listens on the two test
// ports lets the server commands end on their own (engine.mjs exits with its engine).
import { execFileSync } from "node:child_process";

function listeners(ports: string[]): number[] {
  if (process.platform !== "win32") return [];
  try {
    const out = execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        `Get-NetTCPConnection -State Listen -LocalPort ${ports.join(",")} -ErrorAction SilentlyContinue | ForEach-Object OwningProcess`,
      ],
      { encoding: "utf8", timeout: 30_000 },
    );
    return [...new Set(out.split(/\s+/).filter(Boolean).map(Number))].filter((pid) => pid > 0);
  } catch {
    return [];
  }
}

export default async function globalTeardown() {
  const ports = [process.env.UI_TEST_ENGINE_PORT, process.env.UI_TEST_UI_PORT].filter(Boolean) as string[];
  for (const pid of listeners(ports)) {
    try {
      process.kill(pid); // TerminateProcess on Windows; no taskkill needed
    } catch {
      // already gone
    }
  }
}
