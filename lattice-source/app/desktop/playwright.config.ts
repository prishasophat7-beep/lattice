// UI stability tests: layout shift, overflow at every window size, and screenshot comparison.
//   npm run test:ui            run them (Microsoft Edge: the same engine as the app's WebView2)
//   npm run test:ui:update     accept the current screens as the new reference screenshots
import { defineConfig } from "@playwright/test";

const ENGINE_PORT = "8799";
const UI_PORT = "1431";
process.env.UI_TEST_ENGINE_PORT = ENGINE_PORT;
process.env.UI_TEST_UI_PORT = UI_PORT;

export default defineConfig({
  testDir: "ui-tests",
  globalSetup: "./ui-tests/global-setup.ts",
  globalTeardown: "./ui-tests/global-teardown.ts", // stops the servers itself (see there)
  fullyParallel: false,
  workers: 1, // one engine, one conversation history: tests run in order
  timeout: 60_000,
  globalTimeout: 10 * 60_000, // the whole run normally takes 2-3 minutes: never hang longer
  retries: 0, // a test that only passes on retry is the instability we are looking for
  reporter: [["list"], ["html", { open: "never", outputFolder: "ui-tests/report" }]],
  outputDir: "ui-tests/.results",
  snapshotPathTemplate: "ui-tests/screenshots/{arg}{ext}",
  expect: {
    toHaveScreenshot: { timeout: 15_000, maxDiffPixelRatio: 0.002, animations: "disabled", caret: "hide" },
  },
  use: {
    channel: "msedge",
    // a test Edge that closes when the tests end (no background mode or startup boost)
    launchOptions: { args: ["--disable-background-mode", "--disable-features=msEdgeStartupBoost"] },
    baseURL: `http://localhost:${UI_PORT}`,
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "Asia/Kolkata",
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node ui-tests/engine.mjs",
      url: `http://127.0.0.1:${ENGINE_PORT}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 }, // then force-stopped: a run never hangs on exit
      stdout: "ignore",
    },
    {
      command: `node node_modules/vite/bin/vite.js --port ${UI_PORT} --strictPort`,
      url: `http://localhost:${UI_PORT}`,
      timeout: 60_000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 }, // then force-stopped: a run never hangs on exit
      env: { VITE_BACKEND_URL: `http://127.0.0.1:${ENGINE_PORT}`, VITE_API_TOKEN: "" },
    },
  ],
});
