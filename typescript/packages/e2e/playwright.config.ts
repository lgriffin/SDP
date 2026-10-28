// Browser tests of the evidence UI. The UI is built and served with the fixture evidence source, whose
// data are real sdp-api answers over the in-memory kernel; the full-stack run (T7) points SDP_API_URL
// at a live API instead.
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.SDP_UI_PORT ?? 4173);

export default defineConfig({
  testDir: "tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["list"], ["junit", { outputFile: "../../../reports/junit-ts-e2e.xml" }], ["html", { open: "never" }]]
    : [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "pnpm --filter @sdp/evidence-ui build && node ../evidence-ui/build/index.js",
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { SDP_UI_FIXTURES: "1", PORT: String(port), HOST: "127.0.0.1" },
  },
});
