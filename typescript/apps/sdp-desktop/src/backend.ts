// Starts what the compose stack starts, without containers: PostgreSQL, the migrations, sdp-api and the
// evidence UI's Node server. The Electron main process and the smoke test both call startBackend.
import type { ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { devPromptScript, type DevConfig } from "./dev.ts";
import { bundledProfiles, type Layout, logs, profiles, python, repoRoot, runtime, ui, venvBin } from "./layout.ts";
import { type Database, startDatabase, stopDatabase } from "./postgres.ts";
import { choosePort, openLog, run, start, waitForHttp } from "./process.ts";

/** Calls the CLI's entry point with "sdp" as the program name (sdp.cli.app has no __main__ block). */
export const CLI = "import sys; sys.argv[0] = 'sdp'; from sdp.cli.app import main; main()";

/** Disk headroom sdp-api requires before acquiring (DSI-010); the server default of 50 GiB is too much for a laptop. */
export const DESKTOP_MIN_FREE_BYTES = 2 * 1024 ** 3;

/** The environment sdp-api and the SDP Command Prompt share (compare x-kernel in compose.yaml). In
 * developer mode the settings in desktop-dev.json come last, so they override these defaults. */
export function sdpEnvironment(layout: Layout, database: Database, overrides: Record<string, string> = {}): Record<string, string> {
  return {
    ...(layout.checkout ? { SDP_DATA: layout.data } : {}),
    SDP_DATABASE_URL: database.url,
    SDP_ARTIFACT_ROOT: join(layout.data, "artifacts"),
    SDP_LEDGER_DIR: join(layout.data, "ledger"),
    SDP_REPO_ROOT: repoRoot(layout),
    // The person's profiles, so `sdp demo jwst` in the Command Prompt finds the same ones the Start page lists.
    SDP_PROFILES_DIR: profiles(layout),
    // As in the developer stack: a single-user workstation, where signed steps use the person's own key
    // through the SDP Command Prompt (SDP_SIGNING_KEY), and runs from the UI append unsigned entries.
    SDP_ALLOW_UNSIGNED: "1",
    SDP_MIN_FREE_BYTES: String(DESKTOP_MIN_FREE_BYTES),
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    // The database is always the one the app started or checked (externalDatabase reads the override).
    ...Object.fromEntries(Object.entries(overrides).filter(([k]) => k !== "SDP_DATABASE_URL")),
  };
}

/** The database a developer named in desktop-dev.json, which the app uses instead of its own. */
export function externalDatabase(overrides: Record<string, string>): Database | undefined {
  const url = overrides.SDP_DATABASE_URL;
  if (!url) return undefined;
  const parsed = new URL(url);
  return { url, port: parsed.port ? Number(parsed.port) : 5432 };
}

/** sdp-env.cmd, which the SDP Command Prompt calls to reach the running app's database and folders. */
export function commandPromptEnvironment(env: Record<string, string>, pathFirst?: string): string {
  // cmd's `set "K=V"` takes everything up to the closing quote; % would expand, so it is doubled.
  const lines = Object.entries(env).map(([k, v]) => `set "${k}=${v.replaceAll("%", "%%")}"`);
  // Developer mode puts the venv's folder first, so `sdp` is the checkout's command (%PATH% expands here).
  if (pathFirst) lines.push(`set "PATH=${pathFirst.replaceAll("%", "%%")};%PATH%"`);
  return ["@echo off", "rem Written by SDP Desktop on each start. Do not edit.", ...lines, ""].join("\r\n");
}

/** Marks that `sdp integration register` succeeded against this data folder's database. */
export const REGISTERED = "archives-registered";

/** Where seedProfiles records the hash of each default it last shipped. */
export const SHIPPED = ".shipped.json";

const sha256 = (data: Buffer): string => createHash("sha256").update(data).digest("hex");

/** Copy the shipped default profiles (and their guide) into the person's profiles folder. A default the
 * person has not edited is brought up to date by each release; one they edited, or a file of their own
 * with the same name in any letter case, is left alone. */
export async function seedProfiles(from: string, to: string): Promise<string[]> {
  await mkdir(to, { recursive: true });
  let names: string[];
  try {
    names = (await readdir(from)).filter((n) => /\.(ya?ml|md)$/.test(n)).sort();
  } catch {
    return [];
  }
  const shipped: Record<string, string> = await readFile(join(to, SHIPPED), "utf8")
    .then((text) => JSON.parse(text) as Record<string, string>)
    .catch(() => ({}));
  // Windows folders ignore letter case, so FIRST-LOOK.yaml and first-look.yaml are the same file.
  const present = new Map((await readdir(to)).map((n) => [n.toLowerCase(), n]));
  const copied = [];
  for (const name of names) {
    const content = await readFile(join(from, name));
    const hash = sha256(content);
    const existing = present.get(name.toLowerCase());
    if (existing !== undefined) {
      if (existing !== name || shipped[name] === undefined) continue; // the person's own file
      const current = sha256(await readFile(join(to, name)));
      if (current !== shipped[name] || current === hash) continue; // edited, or already current
      await writeFile(join(to, name), content);
    } else {
      try {
        await writeFile(join(to, name), content, { flag: "wx" }); // never replace a file that appeared since
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "EEXIST") continue;
        throw error;
      }
    }
    shipped[name] = hash;
    copied.push(name);
  }
  await writeFile(join(to, SHIPPED), `${JSON.stringify(shipped, null, 2)}\n`);
  return copied;
}

export interface Backend {
  uiUrl: string;
  apiUrl: string;
  /** Required by sdp-api on everything but /health, so other pages in the person's browser cannot drive it. */
  apiToken: string;
  /** The SDP settings the services run with. */
  env: Record<string, string>;
  /** Run `sdp integration register` (what each archive offers, so plans can be drafted). */
  registerArchives(): Promise<void>;
  /** Developer mode's first-start registration, running behind the open app: resolves to the error, or
   * undefined when it succeeded. Absent when the data folder is already registered. */
  firstRegistration?: Promise<Error | undefined>;
  stop(): Promise<void>;
}

export type Progress = (step: string) => void;

/** `sdp integration register` reads MAST's description live; it normally takes about 3 minutes. */
const REGISTER_TIMEOUT_MS = 15 * 60_000;

export async function startBackend(layout: Layout, progress: Progress = () => {}, config: DevConfig = { env: {} }): Promise<Backend> {
  for (const dir of ["logs", "artifacts", "ledger"]) await mkdir(join(layout.data, dir), { recursive: true });
  // The Start page lists this folder: the shipped defaults plus any profile the person adds. A checkout
  // has its own profiles/, listed as they are.
  if (!layout.checkout) await seedProfiles(bundledProfiles(layout), profiles(layout));

  progress("Starting the database");
  const external = externalDatabase(config.env);
  const database = external ?? (await startDatabase(layout));
  const env = sdpEnvironment(layout, database, config.env);
  if (external) await startExternalDatabase(layout, env);
  const apiToken = randomBytes(32).toString("hex");

  let api: ChildProcess | undefined;
  // Ends a registration still running in the background when the app closes.
  const closing = new AbortController();
  const stop = async (): Promise<void> => {
    closing.abort();
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
    // A database the developer named is theirs (the harness scripts may be using it); it stays up.
    if (!external) await stopDatabase(layout);
  };

  try {
    await mkdir(runtime(layout), { recursive: true });
    await writeFile(join(runtime(layout), "sdp-env.cmd"), commandPromptEnvironment(env), { mode: 0o600 });
    const childEnv = { ...process.env, ...env };
    const registerArchives = async (): Promise<void> => {
      const log = openLog(join(logs(layout), "integration.log"));
      try {
        await run(python(layout), ["-c", CLI, "integration", "register"], {
          env: childEnv,
          log,
          timeoutMs: REGISTER_TIMEOUT_MS,
          signal: closing.signal,
        });
        await writeFile(join(runtime(layout), REGISTERED), `${new Date().toISOString()}\n`);
      } finally {
        log.end();
      }
    };

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

    if (layout.checkout) {
      // The developer prompt reaches the running service too, so the UI's live-reload server
      // (`pnpm --filter @sdp/evidence-ui dev`) and curl work there without settings of their own.
      const promptEnv = { ...env, SDP_API_URL: `${apiUrl}/graphql`, SDP_API_TOKEN: apiToken };
      const envFile = join(runtime(layout), "sdp-env.cmd");
      await writeFile(envFile, commandPromptEnvironment(promptEnv, venvBin(layout.checkout, layout.windows)), { mode: 0o600 });
      await writeFile(join(runtime(layout), "sdp-dev-prompt.cmd"), devPromptScript(envFile, layout.checkout));
    }

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
      SDP_PROFILES_DIR: env.SDP_PROFILES_DIR,
      NODE_ENV: "production",
    });
    await import(pathToFileURL(ui(layout)).href);
    await waitForHttp(uiUrl, 30_000);
    // Once per data folder in developer mode, so plans can be drafted from the first start. It runs behind
    // the open app (about 3 minutes, longer if MAST is slow); a failure is reported, and tried again on
    // the next start or from the Developer menu.
    const firstRegistration =
      layout.checkout && !existsSync(join(runtime(layout), REGISTERED))
        ? registerArchives().then(
            () => undefined,
            (error: unknown) => (error instanceof Error ? error : new Error(String(error))),
          )
        : undefined;
    return { uiUrl, apiUrl, apiToken, env, registerArchives, firstRegistration, stop };
  } catch (error) {
    await stop().catch(() => {});
    throw error;
  }
}

/** Start the developer's own local PostgreSQL with `sdp db start` when it is not running (it finds the
 * data folder from SDP_PGDATA or <SDP_DATA>/postgres). A database elsewhere, or one already up, is left
 * as it is; if it cannot be reached, the migrations that follow say so. */
async function startExternalDatabase(layout: Layout, env: Record<string, string>): Promise<void> {
  const log = openLog(join(logs(layout), "postgres.log"));
  try {
    await run(python(layout), ["-c", CLI, "db", "start"], {
      env: { ...process.env, ...env },
      log,
      timeoutMs: 10 * 60_000,
    });
  } catch {
    // Not a local cluster this checkout can start: the migrations report the real problem.
  } finally {
    log.end();
  }
}
