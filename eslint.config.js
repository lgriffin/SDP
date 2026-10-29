// ESLint for the TypeScript workspace (§11.7 static stage).
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["**/node_modules/**", "**/src/generated/**", "**/dist/**", "**/build/**", "**/.svelte-kit/**", "**/test-results/**", "**/playwright-report/**", "typescript/apps/sdp-desktop/resources/**", "typescript/apps/sdp-desktop/release/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.mjs"],
    languageOptions: { globals: { process: "readonly", console: "readonly" } },
  },
  {
    rules: {
      // Fakes over mocks (ADR-006, TST-005): doubles live in each package's src/testing.
      "no-restricted-properties": [
        "error",
        { object: "vi", property: "mock", message: "Use a fake from src/testing, not vi.mock (TST-005)." },
        { object: "vi", property: "spyOn", message: "Use a fake from src/testing, not vi.spyOn (TST-005)." },
      ],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
);
