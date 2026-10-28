// T0 for TypeScript (§11.3): spec-harness and evidence-ui import only @sdp/contracts from the workspace.
/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "harness-imports-only-contracts",
      comment: "The harness is a separate hexagon; its only workspace dependency is the generated contracts.",
      severity: "error",
      from: { path: "^typescript/packages/spec-harness/" },
      to: { path: "^typescript/packages/(?!spec-harness/|contracts/)" },
    },
    {
      name: "ui-imports-only-contracts",
      severity: "error",
      from: { path: "^typescript/packages/evidence-ui/" },
      to: { path: "^typescript/packages/(?!evidence-ui/|contracts/)" },
    },
    {
      name: "contracts-import-nothing-from-the-workspace",
      severity: "error",
      from: { path: "^typescript/packages/contracts/" },
      to: { path: "^typescript/packages/(?!contracts/)" },
    },
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    exclude: { path: "(node_modules|/generated/|/\.svelte-kit/|/build/|/test-results/|/playwright-report/)" },
    tsPreCompilationDeps: true,
    combinedDependencies: true,
    preserveSymlinks: false,
  },
};
