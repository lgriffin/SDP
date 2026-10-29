// The embedded PostgreSQL 16: created on first start, started and stopped with the app. It listens on
// loopback only, with a password generated at creation and kept in the user's data folder.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { appendFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { type Layout, logs, pgdata, postgresBin, secrets } from "./layout.ts";
import { choosePort, openLog, run } from "./process.ts";

export const USER = "sdp";
export const DATABASE = "postgres";
const PREFERRED_PORT = 54329;

export interface Database {
  url: string;
  port: number;
}

/** Settings appended to postgresql.conf when the cluster is created. */
export const CLUSTER_SETTINGS = [
  "# Written by SDP Desktop: loopback only, no Unix sockets.",
  "listen_addresses = '127.0.0.1'",
  "unix_socket_directories = ''",
  "",
].join("\n");

export const databaseUrl = (password: string, port: number): string =>
  `postgresql://${USER}:${encodeURIComponent(password)}@127.0.0.1:${port}/${DATABASE}`;

/** The port in postmaster.pid (its fourth line) of a server that is already running. */
export function portFromPid(contents: string): number | undefined {
  const port = Number.parseInt(contents.split(/\r?\n/)[3] ?? "", 10);
  return Number.isInteger(port) && port > 0 ? port : undefined;
}

async function password(layout: Layout): Promise<string> {
  return (await readFile(join(secrets(layout), "database-password"), "utf8")).trim();
}

async function create(layout: Layout): Promise<void> {
  const dir = pgdata(layout);
  await rm(dir, { recursive: true, force: true });
  await mkdir(secrets(layout), { recursive: true });
  const secret = randomBytes(24).toString("hex");
  await writeFile(join(secrets(layout), "database-password"), secret, { mode: 0o600 });
  const pwfile = join(secrets(layout), "initdb-pwfile");
  await writeFile(pwfile, secret, { mode: 0o600 });
  const log = openLog(join(logs(layout), "postgres.log"));
  try {
    await run(
      postgresBin(layout, "initdb"),
      ["-D", dir, "-U", USER, "--pwfile", pwfile, "-A", "scram-sha-256", "-E", "UTF8", "--no-locale"],
      { log },
    );
    await appendFile(join(dir, "postgresql.conf"), `\n${CLUSTER_SETTINGS}`);
  } catch (error) {
    // A half-made cluster would block the next attempt; start again from nothing next time.
    await rm(dir, { recursive: true, force: true });
    throw error;
  } finally {
    await rm(pwfile, { force: true });
    log.end();
  }
}

function status(layout: Layout): Promise<number | null> {
  return new Promise((resolve, reject) => {
    const child = spawn(postgresBin(layout, "pg_ctl"), ["status", "-D", pgdata(layout)], { windowsHide: true, stdio: "ignore" });
    child.on("error", reject);
    child.on("exit", resolve);
  });
}

/** Create the cluster if needed and start it; a server left running by an earlier session is reused. */
export async function startDatabase(layout: Layout): Promise<Database> {
  await mkdir(logs(layout), { recursive: true });
  if (!existsSync(join(pgdata(layout), "PG_VERSION"))) await create(layout);
  const secret = await password(layout);
  if ((await status(layout)) === 0) {
    const port = portFromPid(await readFile(join(pgdata(layout), "postmaster.pid"), "utf8"));
    if (port) return { url: databaseUrl(secret, port), port };
  }
  const port = await choosePort(PREFERRED_PORT);
  const log = openLog(join(logs(layout), "postgres.log"));
  try {
    await run(
      postgresBin(layout, "pg_ctl"),
      ["start", "-D", pgdata(layout), "-w", "-t", "120", "-l", join(logs(layout), "postgres-server.log"), "-o", `-p ${port}`],
      { log },
    );
  } finally {
    log.end();
  }
  return { url: databaseUrl(secret, port), port };
}

export async function stopDatabase(layout: Layout): Promise<void> {
  if ((await status(layout)) !== 0) return;
  const log = openLog(join(logs(layout), "postgres.log"));
  try {
    await run(postgresBin(layout, "pg_ctl"), ["stop", "-D", pgdata(layout), "-m", "fast", "-w", "-t", "60"], { log });
  } finally {
    log.end();
  }
}
