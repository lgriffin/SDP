// Unit tests cover the ports, adapters and view logic, which are plain TypeScript; no Svelte plugin needed.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    reporters: process.env.CI ? ["default", "junit"] : ["default"],
    outputFile: { junit: "../../../reports/junit-ts-evidence-ui.xml" },
  },
});
