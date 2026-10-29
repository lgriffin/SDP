// Starts what the compose stack starts, without containers: PostgreSQL, the migrations, sdp-api and the
// evidence UI's Node server. The Electron main process and the smoke test both call startBackend.
import type { ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { copyFile, mkdir, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { bundledProfiles, type Layout, logs, profiles, python, repoRoot, runtime, ui } from "./layout.ts";
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

/** Copy the shipped default profiles (and their guide) into the person's profiles folder. A file
 * already there is left alone, so edits survive; a new default version arrives under its own name. */
export async function seedProfiles(from: string, to: string): Promise<string[]> {
  await mkdir(to, { recursive: true });
  let names: string[];
  try {
    names = (await readdir(from)).filter((n) => /\.(ya?ml|md)$/.test(n));
  } catch {
    return [];
  }
  const present = new Set(await readdir(to));
  const copied = [];
  for (const name of names.filter((n) => !present.has(n)).sort()) {
    await copyFile(join(from, name), join(to, name));
    copied.push(name);
  }
  return copied;
}

export interface Backend {
  uiUrl: string;
  apiUrl: string;
  /** Required by sdp-api on everything but /health, so other pages in the person's browser cannot drive it. */
  apiToken: string;
  stop(): Promise<void>;
}

export type Progress = (step: string) => void;

export async function startBackend(layout: Layout, progress: Progress = () => {}): Promise<Backend> {
  for (const dir of ["logs", "artifacts", "ledger"]) await mkdir(join(layout.data, dir), { recursive: true });
  // The Start page lists this folder: the shipped defaults plus any profile the person adds.
  await seedProfiles(bundledProfiles(layout), profiles(layout));

  progress("Starting the database");
  const database = await startDatabase(layout);
  const env = sdpEnvironment(layout, database);
  const apiToken = randomBytes(32).toString("hex");

  let api: ChildProcess | undefined;
  const stop = async (): Promise<void> => {
    if (api && api.exitCode === null) {
      const exited = new Promise((r) => api?.once("exit", r));
      api.kill();
      const stopped = await Promise.race([exited.then(() => true), new Promise((r) => setTimeout(r, 5000, false))]);
      // On Windows kill() already terminates; elsewhere a server that ignored SIGTERM is killed outright.
      if (!stopped && api.exitCode === null) {
        api.kill("SIGKILL");
        await Promise.race([exited, new Promise((r) => setTimeout(r, 2000))]);
      }
    }
    await stopDatabase(layout);
  };

  try {
    await mkdir(runtime(layout), { recursive: true });
    await writeFile(join(runtime(layout), "sdp-env.cmd"), commandPromptEnvironment(env), { mode: 0o600 });
    const childEnv = { ...process.env, ...env };

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
      env: { ...childEnv, SDP_API_TOKEN: apiToken },
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
      SDP_API_TOKEN: apiToken,
      SDP_PROFILES_DIR: profiles(layout),
      NODE_ENV: "production",
    });
    await import(pathToFileURL(ui(layout)).href);
    await waitForHttp(uiUrl, 30_000);
    return { uiUrl, apiUrl, apiToken, stop };
  } catch (error) {
    await stop().catch(() => {});
    throw error;
  }
}
