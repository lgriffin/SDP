// Child processes, ports and readiness checks for the bundled services.
import { type ChildProcess, spawn } from "node:child_process";
import { createWriteStream, type WriteStream } from "node:fs";
import { request } from "node:http";
import { createServer } from "node:net";

export interface RunOptions {
  env?: NodeJS.ProcessEnv;
  cwd?: string;
  log: WriteStream;
}

/** Start a process whose output goes to the log; on Windows no console window appears. */
export function start(command: string, args: string[], options: RunOptions): ChildProcess {
  options.log.write(`\n$ ${command} ${args.join(" ")}\n`);
  const child = spawn(command, args, { env: options.env, cwd: options.cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout?.pipe(options.log, { end: false });
  child.stderr?.pipe(options.log, { end: false });
  return child;
}

export class CommandFailed extends Error {}

/** Run a process to completion; a non-zero exit is an error naming the log to read. */
export function run(command: string, args: string[], options: RunOptions): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = start(command, args, options);
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new CommandFailed(`${command} ${args[0] ?? ""} exited with code ${code} (see ${options.log.path})`));
    });
  });
}

export const openLog = (path: string): WriteStream => createWriteStream(path, { flags: "a" });

export function portIsFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

/** The preferred port when it is free (so the address stays the same between starts), else any free one. */
export async function choosePort(preferred: number): Promise<number> {
  if (await portIsFree(preferred)) return preferred;
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => (typeof address === "object" && address ? resolve(address.port) : reject(new Error("no free port"))));
    });
  });
}

/** Resolve once the URL answers with any HTTP status; a child that exits first fails the wait. */
export async function waitForHttp(url: string, timeoutMs: number, child?: ChildProcess): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let exited: number | null | undefined;
  child?.once("exit", (code) => (exited = code));
  while (Date.now() < deadline) {
    if (exited !== undefined) throw new CommandFailed(`the process serving ${url} exited with code ${exited}`);
    if (await answers(url)) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new CommandFailed(`${url} did not answer within ${Math.round(timeoutMs / 1000)} seconds`);
}

function answers(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const req = request(url, { method: "GET", timeout: 2000 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => req.destroy());
    req.end();
  });
}
