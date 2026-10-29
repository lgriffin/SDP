import { describe, expect, it } from "vitest";

import { commandPromptEnvironment, DESKTOP_MIN_FREE_BYTES, sdpEnvironment } from "../src/backend.ts";
import { defaultDataDir, postgresBin, python } from "../src/layout.ts";
import { databaseUrl, portFromPid } from "../src/postgres.ts";
import { choosePort, portIsFree } from "../src/process.ts";

const windows = { resources: "C:\\SDP\\resources\\sdp", data: "C:\\Users\\a\\AppData\\Local\\SDP", windows: true };
const linux = { resources: "/opt/sdp/resources/sdp", data: "/home/a/.local/share/sdp", windows: false };

describe("layout", () => {
  it("finds the bundled executables for each platform", () => {
    expect(python(windows)).toMatch(/python[\\/]python\.exe$/);
    expect(python(linux)).toBe("/opt/sdp/resources/sdp/python/bin/python3");
    expect(postgresBin(windows, "pg_ctl")).toMatch(/postgres[\\/]bin[\\/]pg_ctl\.exe$/);
    expect(postgresBin(linux, "initdb")).toBe("/opt/sdp/resources/sdp/postgres/bin/initdb");
  });

  it("keeps data in the local, not roaming, profile on Windows", () => {
    expect(defaultDataDir({ LOCALAPPDATA: "C:\\Users\\a\\AppData\\Local" }, "C:\\Users\\a", true)).toMatch(/AppData.Local.SDP$/);
    expect(defaultDataDir({}, "/home/a", false)).toBe("/home/a/.local/share/sdp");
    expect(defaultDataDir({ XDG_DATA_HOME: "/x" }, "/home/a", false)).toBe("/x/sdp");
  });
});

describe("database", () => {
  it("builds a loopback URL with the password escaped", () => {
    expect(databaseUrl("p@ss/word", 54329)).toBe("postgresql://sdp:p%40ss%2Fword@127.0.0.1:54329/postgres");
  });

  it("reads the port of a server already running from postmaster.pid", () => {
    expect(portFromPid("4242\r\nC:/data/postgres\r\n1730160000\r\n54330\r\n\r\n127.0.0.1\r\n")).toBe(54330);
    expect(portFromPid("4242\n/data\n")).toBeUndefined();
  });
});

describe("environment", () => {
  const env = sdpEnvironment(linux, { url: "postgresql://sdp:x@127.0.0.1:1/postgres", port: 1 });

  it("gives sdp-api the data folder, the bundled repository data and a laptop-sized disk margin", () => {
    expect(env.SDP_ARTIFACT_ROOT).toBe("/home/a/.local/share/sdp/artifacts");
    expect(env.SDP_LEDGER_DIR).toBe("/home/a/.local/share/sdp/ledger");
    expect(env.SDP_REPO_ROOT).toBe("/opt/sdp/resources/sdp/repo");
    expect(Number(env.SDP_MIN_FREE_BYTES)).toBe(DESKTOP_MIN_FREE_BYTES);
  });

  it("writes the Command Prompt settings as cmd set lines, with % escaped", () => {
    const script = commandPromptEnvironment({ SDP_DATABASE_URL: "postgresql://sdp:a%b@h/p" });
    expect(script.split("\r\n")).toContain('set "SDP_DATABASE_URL=postgresql://sdp:a%%b@h/p"');
    expect(script.startsWith("@echo off\r\n")).toBe(true);
  });
});

describe("ports", () => {
  it("falls back to a free port when the preferred one is taken", async () => {
    const { createServer } = await import("node:net");
    const server = createServer();
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const taken = (server.address() as { port: number }).port;
    expect(await portIsFree(taken)).toBe(false);
    const chosen = await choosePort(taken);
    expect(chosen).not.toBe(taken);
    await new Promise((r) => server.close(r));
  });
});
