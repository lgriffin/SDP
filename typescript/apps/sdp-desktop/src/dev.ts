// Developer mode: the app run from a repository checkout (`pnpm --filter @sdp/desktop dev`, or the
// "SDP (dev)" shortcut that setup-dev.ps1 makes). It starts the same services as the installed app, but
// from the checkout's venv, UI build and portable PostgreSQL, so a code change needs no staging.
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { venv } from "./layout.ts";

export interface DevOptions {
  /** True when the app should run from its checkout. */
  dev: boolean;
  /** --sdp-data=<folder>: the data folder, instead of the default. */
  data?: string;
}

/** Reads `--sdp-dev` and `--sdp-data=<folder>` (or `--sdp-data <folder>`) from the command line; the
 * SDP_DESKTOP_DEV=1 environment variable also turns developer mode on. */
export function parseDevOptions(argv: readonly string[], env: NodeJS.ProcessEnv): DevOptions {
  const options: DevOptions = { dev: env.SDP_DESKTOP_DEV === "1" };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i] ?? "";
    if (arg === "--sdp-dev") options.dev = true;
    else if (arg.startsWith("--sdp-data=")) options.data = arg.slice("--sdp-data=".length);
    else if (arg === "--sdp-data" && argv[i + 1]) options.data = argv[++i];
  }
  return options;
}

/** The checkout holding this app: dist/ (or src/) sits in <checkout>/typescript/apps/sdp-desktop. */
export const checkoutOf = (appDir: string): string => resolve(appDir, "..", "..", "..", "..");

/** The default data folder in developer mode, kept apart from the installed app's %LOCALAPPDATA%\SDP. */
export const DEV_DATA_NAME = "SDP-dev";

/** What a developer checkout must have before the app can start from it, as advice to show. */
export function checkoutProblems(
  checkout: string,
  windows: boolean,
  platform: string = process.platform,
  arch: string = process.arch,
): string[] {
  const problems: string[] = [];
  // The portable PostgreSQL the checkout brings (package.json's optionalDependencies) exists for these only.
  if (arch !== "x64" || !(platform === "win32" || platform === "linux")) {
    problems.push(
      `Developer mode runs on Windows x64 and Linux x64, not ${platform} ${arch}: there is no portable PostgreSQL for it. ` +
        "Use the manual route in docs/guides/install-dev-tools.md.",
    );
    return problems;
  }
  if (!existsSync(join(checkout, "python", "pyproject.toml"))) {
    problems.push(`${checkout} is not an SDP checkout (no python/pyproject.toml).`);
    return problems;
  }
  if (!existsSync(venv(checkout))) problems.push("The Python environment is missing: run `uv sync --frozen` in the python folder.");
  if (!existsSync(join(checkout, "typescript", "packages", "evidence-ui", "build", "index.js"))) {
    problems.push("The UI is not built: run `pnpm --filter @sdp/evidence-ui build`.");
  }
  const pg = join(checkout, "typescript", "apps", "sdp-desktop", "node_modules", "@embedded-postgres", windows ? "windows-x64" : "linux-x64");
  if (!existsSync(pg)) problems.push("The portable PostgreSQL is missing: run `pnpm install` in the checkout.");
  return problems;
}

/** <data>/desktop-dev.json: settings the developer keeps, written by setup-dev.ps1 and editable by hand. */
export const DEV_CONFIG = "desktop-dev.json";

export interface DevConfig {
  /** Extra or overriding SDP settings, for example SDP_GAIA_SOURCE or SDP_SIGNING_KEY. With
   * SDP_DATABASE_URL set, the app uses that database rather than its own (see externalDatabase). */
  env: Record<string, string>;
}

export async function readDevConfig(data: string): Promise<DevConfig> {
  let text: string;
  try {
    text = await readFile(join(data, DEV_CONFIG), "utf8");
  } catch {
    return { env: {} };
  }
  // PowerShell 5.1 writes UTF-8 with a byte-order mark.
  const parsed = JSON.parse(text.replace(/^\uFEFF/, "")) as { env?: Record<string, unknown> };
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(parsed.env ?? {})) {
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") env[k] = String(v);
  }
  return { env };
}

/** Lines for the developer prompt, the console the Developer menu opens. */
export function devPromptScript(envFile: string, checkout: string): string {
  const q = (s: string): string => s.replaceAll("%", "%%");
  return [
    "@echo off",
    "rem Written by SDP (dev) on each start. Do not edit.",
    "title SDP developer prompt",
    `call "${q(envFile)}"`,
    `cd /d "${q(checkout)}"`,
    "echo SDP developer prompt: this checkout's code, connected to the running SDP (dev) app.",
    "echo   sdp --help                            every command (no uv run needed)",
    "echo   pnpm --filter @sdp/evidence-ui dev    the UI with live reload, against the running service",
    "echo   uv sync --frozen  (in python)         after a change to Python dependencies",
    "",
  ].join("\r\n");
}
