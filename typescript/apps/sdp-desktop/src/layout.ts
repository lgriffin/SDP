// Where the bundled pieces and the user's data live. Pure path logic, shared by the Electron main
// process, the smoke test and the unit tests.
import { join } from "node:path";

export interface Layout {
  /** resources/sdp in the installed app (scripts/stage.sh fills it). */
  resources: string;
  /** The user's SDP folder: database, artifacts, ledger and logs. */
  data: string;
  windows: boolean;
  /** Developer mode: the repository checkout the app runs from. Python, PostgreSQL, the UI build,
   * contracts and profiles then come straight from the checkout, so nothing has to be staged. */
  checkout?: string;
}

/** Where `pnpm install` puts the portable PostgreSQL 16 in a checkout. */
const checkoutPostgres = (checkout: string, windows: boolean): string =>
  join(checkout, "typescript", "apps", "sdp-desktop", "node_modules", "@embedded-postgres", windows ? "windows-x64" : "linux-x64", "native");

/** The checkout's virtual environment (`uv sync --frozen` in python/). */
export const venv = (checkout: string): string => join(checkout, "python", ".venv");
/** The folder holding the venv's executables, the `sdp` command among them. */
export const venvBin = (checkout: string, windows: boolean): string => join(venv(checkout), windows ? "Scripts" : "bin");

export const python = (l: Layout): string =>
  l.checkout
    ? join(venvBin(l.checkout, l.windows), l.windows ? "python.exe" : "python")
    : l.windows ? join(l.resources, "python", "python.exe") : join(l.resources, "python", "bin", "python3");

export const postgresBin = (l: Layout, tool: "initdb" | "pg_ctl"): string =>
  join(l.checkout ? checkoutPostgres(l.checkout, l.windows) : join(l.resources, "postgres"), "bin", l.windows ? `${tool}.exe` : tool);

export const ui = (l: Layout): string =>
  l.checkout ? join(l.checkout, "typescript", "packages", "evidence-ui", "build", "index.js") : join(l.resources, "ui", "build", "index.js");

/** The repository data sdp-api reads at run time: contracts and experiments (as in the kernel image). */
export const repoRoot = (l: Layout): string => l.checkout ?? join(l.resources, "repo");

/** The default test profiles shipped with the app (the repository's profiles/ folder). */
export const bundledProfiles = (l: Layout): string => join(l.resources, "profiles");
/** The person's profiles folder, which the Start page lists (SDP_PROFILES_DIR). In developer mode it is
 * the checkout's own profiles/, so an edited profile is the one under version control. */
export const profiles = (l: Layout): string => (l.checkout ? join(l.checkout, "profiles") : join(l.data, "profiles"));

export const pgdata = (l: Layout): string => join(l.data, "postgres");
export const logs = (l: Layout): string => join(l.data, "logs");
export const secrets = (l: Layout): string => join(l.data, "secrets");
/** Rewritten on every start so the SDP Command Prompt reaches the running database. */
export const runtime = (l: Layout): string => join(l.data, "runtime");

/** The default data folder: %LOCALAPPDATA%\SDP on Windows (not roamed), ~/.local/share/sdp elsewhere. */
export function defaultDataDir(env: NodeJS.ProcessEnv, home: string, windows: boolean): string {
  if (windows) return join(env.LOCALAPPDATA ?? join(home, "AppData", "Local"), "SDP");
  return join(env.XDG_DATA_HOME ?? join(home, ".local", "share"), "sdp");
}
