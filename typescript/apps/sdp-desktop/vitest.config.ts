// Unit tests of the launcher's pure logic; the whole start-up is covered by the smoke test (src/smoke.ts).
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    reporters: process.env.CI ? ["default", "junit"] : ["default"],
    outputFile: { junit: "../../../reports/junit-ts-desktop.xml" },
  },
});
