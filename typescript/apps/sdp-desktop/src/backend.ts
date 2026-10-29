// Starts what the compose stack starts, without containers: PostgreSQL, the migrations, sdp-api and the
// evidence UI's Node server. The Electron main process and the smoke test both call startBackend.
import type { ChildProcess } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { type Layout, logs, python, repoRoot, runtime, ui } from "./layout.ts";
import { type Database, startDatabase, stopDatabase } from "./postgres.ts";
import { choosePort, openLog, run, start, waitForHttp } from "./process.ts";

/** Calls the CLI's entry point with "sdp" as the program name (sdp.cli.app has no __main__ block). */
export const CLI = "import sys; sys.argv[0] = 'sdp'; from sdp.cli.app import main; main()";

/** Disk headroom sdp-api requires before acquiring (DSI-010); the server default of 50 GiB is too much for a laptop. */
export const DESKTOP_MIN_FREE_BYTES = 2 * 1024 ** 3;

/** The environment sdp-api and the SDP Command Prompt share (compare x-kernel in compose.yaml). */
export function sdpEnvironment(layout: Layout, database: Database): Record<string, string> {
  return {
    SDP_DATABASE_URL: database.url,
    SDP_ARTIFACT_ROOT: join(layout.data, "artifacts"),
    SDP_LEDGER_DIR: join(layout.data, "ledger"),
    SDP_REPO_ROOT: repoRoot(layout),
    // As in the developer stack: a single-user workstation, where signed steps use the person's own key
    // through the SDP Command Prompt (SDP_SIGNING_KEY), and runs from the UI append unsigned entries.
    SDP_ALLOW_UNSIGNED: "1",
    SDP_MIN_FREE_BYTES: String(DESKTOP_MIN_FREE_BYTES),
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
  };
}

/** sdp-env.cmd, which the SDP Command Prompt calls to reach the running app's database and folders. */
export function commandPromptEnvironment(env: Record<string, string>): string {
  // cmd's `set "K=V"` takes everything up to the closing quote; % would expand, so it is doubled.
  const lines = Object.entries(env).map(([k, v]) => `set "${k}=${v.replaceAll("%", "%%")}"`);
  return ["@echo off", "rem Written by SDP Desktop on each start. Do not edit.", ...lines, ""].join("\r\n");
}

export interface Backend {
  uiUrl: string;
  apiUrl: string;
  stop(): Promise<void>;
}

export type Progress = (step: string) => void;

export async function startBackend(layout: Layout, progress: Progress = () => {}): Promise<Backend> {
  await mkdir(logs(layout), { recursive: true });
  for (const dir of ["artifacts", "ledger"]) await mkdir(join(layout.data, dir), { recursive: true });

  progress("Starting the database");
  const database = await startDatabase(layout);
  const env = sdpEnvironment(layout, database);
  const childEnv = { ...process.env, ...env };
  await mkdir(runtime(layout), { recursive: true });
  await writeFile(join(runtime(layout), "sdp-env.cmd"), commandPromptEnvironment(env), { mode: 0o600 });

  let api: ChildProcess | undefined;
  const stop = async (): Promise<void> => {
    if (api && api.exitCode === null) {
      const exited = new Promise((r) => api?.once("exit", r));
      api.kill();
      await Promise.race([exited, new Promise((r) => setTimeout(r, 5000))]);
    }
    await stopDatabase(layout);
  };

  try {
    progress("Preparing the database");
    const migrateLog = openLog(join(logs(layout), "migrate.log"));
    try {
      await run(python(layout), ["-c", CLI, "db", "migrate"], { env: childEnv, log: migrateLog });
    } finally {
      migrateLog.end();
    }

    progress("Starting the SDP service");
    const apiPort = await choosePort(54380);
    const apiUrl = `http://127.0.0.1:${apiPort}`;
    api = start(python(layout), ["-m", "sdp.api.serve", "--host", "127.0.0.1", "--port", String(apiPort)], {
      env: childEnv,
      cwd: layout.data,
      log: openLog(join(logs(layout), "api.log")),
    });
    // The first import of astropy and scipy is slow, more so while antivirus scans new files.
    await waitForHttp(`${apiUrl}/health`, 180_000, api);

    progress("Opening the workspace");
    const uiPort = await choosePort(54381);
    const uiUrl = `http://127.0.0.1:${uiPort}`;
    // adapter-node reads these when its index.js loads, and listens in this process.
    Object.assign(process.env, {
      HOST: "127.0.0.1",
      PORT: String(uiPort),
      ORIGIN: uiUrl,
      SDP_API_URL: `${apiUrl}/graphql`,
      NODE_ENV: "production",
    });
    await import(pathToFileURL(ui(layout)).href);
    await waitForHttp(uiUrl, 30_000);
    return { uiUrl, apiUrl, stop };
  } catch (error) {
    await stop().catch(() => {});
    throw error;
  }
}
