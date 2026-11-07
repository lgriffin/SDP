import { describe, expect, it } from "vitest";

import { mkdir, mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  commandPromptEnvironment,
  DESKTOP_MIN_FREE_BYTES,
  externalDatabase,
  sdpEnvironment,
  seedProfiles,
  SHIPPED,
} from "../src/backend.ts";
import { checkoutOf, checkoutProblems, DEV_CONFIG, devPromptScript, parseDevOptions, readDevConfig } from "../src/dev.ts";
import { defaultDataDir, postgresBin, profiles, python, repoRoot, ui, venvBin } from "../src/layout.ts";
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
    expect(env.SDP_PROFILES_DIR).toBe("/home/a/.local/share/sdp/profiles");
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

describe("default profiles", () => {
  it("are copied into the person's folder without overwriting a profile already there", async () => {
    const from = await mkdtemp(join(tmpdir(), "bundled-"));
    const to = await mkdtemp(join(tmpdir(), "mine-"));
    await writeFile(join(from, "first-look.yaml"), "shipped");
    await writeFile(join(from, "star-history.yaml"), "shipped");
    await writeFile(join(from, "README.md"), "guide");
    await writeFile(join(from, "notes.txt"), "not a profile");
    await writeFile(join(to, "first-look.yaml"), "the person's own");

    expect(await seedProfiles(from, to)).toEqual(["README.md", "star-history.yaml"]);
    expect(await readFile(join(to, "first-look.yaml"), "utf8")).toBe("the person's own");
    expect((await readdir(to)).filter((n) => n !== SHIPPED).sort()).toEqual(["README.md", "first-look.yaml", "star-history.yaml"]);
    expect(await seedProfiles(join(from, "missing"), to)).toEqual([]);
  });

  it("update the defaults the person has not edited, and only those", async () => {
    const from = await mkdtemp(join(tmpdir(), "bundled-"));
    const to = await mkdtemp(join(tmpdir(), "mine-"));
    await writeFile(join(from, "first-look.yaml"), "1.0.0");
    await writeFile(join(from, "star-history.yaml"), "1.0.0");
    await seedProfiles(from, to);
    await writeFile(join(to, "star-history.yaml"), "edited by the person");

    await writeFile(join(from, "first-look.yaml"), "1.1.0");
    await writeFile(join(from, "star-history.yaml"), "1.1.0");
    expect(await seedProfiles(from, to)).toEqual(["first-look.yaml"]);
    expect(await readFile(join(to, "first-look.yaml"), "utf8")).toBe("1.1.0");
    expect(await readFile(join(to, "star-history.yaml"), "utf8")).toBe("edited by the person");
    expect(await seedProfiles(from, to)).toEqual([]);
  });

  it("leave a file of the person's whose name differs only in letter case", async () => {
    const from = await mkdtemp(join(tmpdir(), "bundled-"));
    const to = await mkdtemp(join(tmpdir(), "mine-"));
    await writeFile(join(from, "first-look.yaml"), "shipped");
    await writeFile(join(to, "FIRST-LOOK.yaml"), "the person's own");

    expect(await seedProfiles(from, to)).toEqual([]);
    expect((await readdir(to)).filter((n) => n !== SHIPPED)).toEqual(["FIRST-LOOK.yaml"]);
  });
});

describe("developer mode", () => {
  const devWindows = { ...windows, data: "D:\\SDP", checkout: "D:\\code\\SDP" };
  const devLinux = { ...linux, data: "/home/a/sdp-dev", checkout: "/home/a/SDP" };

  it("reads --sdp-dev and --sdp-data in either form, or SDP_DESKTOP_DEV", () => {
    expect(parseDevOptions(["electron", ".", "--sdp-dev", "--sdp-data=D:\\SDP"], {})).toEqual({ dev: true, data: "D:\\SDP" });
    expect(parseDevOptions(["electron", ".", "--sdp-data", "/x"], {})).toEqual({ dev: false, data: "/x" });
    expect(parseDevOptions(["electron", "."], { SDP_DESKTOP_DEV: "1" })).toEqual({ dev: true });
    expect(parseDevOptions(["electron", "."], {})).toEqual({ dev: false });
  });

  it("finds the checkout from the app's own folder", () => {
    expect(checkoutOf("/home/a/SDP/typescript/apps/sdp-desktop/dist")).toBe("/home/a/SDP");
  });

  it("runs everything from the checkout: venv Python, pnpm's PostgreSQL, the UI build, contracts and profiles", () => {
    expect(python(devWindows)).toMatch(/python[\\/]\.venv[\\/]Scripts[\\/]python\.exe$/);
    expect(python(devLinux)).toBe("/home/a/SDP/python/.venv/bin/python");
    expect(postgresBin(devLinux, "pg_ctl")).toBe(
      "/home/a/SDP/typescript/apps/sdp-desktop/node_modules/@embedded-postgres/linux-x64/native/bin/pg_ctl",
    );
    expect(postgresBin(devWindows, "initdb")).toMatch(/windows-x64[\\/]native[\\/]bin[\\/]initdb\.exe$/);
    expect(ui(devLinux)).toBe("/home/a/SDP/typescript/packages/evidence-ui/build/index.js");
    expect(repoRoot(devLinux)).toBe("/home/a/SDP");
    expect(profiles(devLinux)).toBe("/home/a/SDP/profiles");
    expect(venvBin("/home/a/SDP", false)).toBe("/home/a/SDP/python/.venv/bin");
  });

  it("lets desktop-dev.json override the defaults, but never the database the app checked", () => {
    const env = sdpEnvironment(devLinux, { url: "postgresql://sdp:x@127.0.0.1:1/postgres", port: 1 }, {
      SDP_ARTIFACT_ROOT: "/big/artifacts",
      SDP_GAIA_SOURCE: "/big/gaia",
      SDP_DATABASE_URL: "postgresql://elsewhere/sdp",
    });
    expect(env.SDP_DATA).toBe("/home/a/sdp-dev");
    expect(env.SDP_REPO_ROOT).toBe("/home/a/SDP");
    expect(env.SDP_ARTIFACT_ROOT).toBe("/big/artifacts");
    expect(env.SDP_GAIA_SOURCE).toBe("/big/gaia");
    expect(env.SDP_DATABASE_URL).toBe("postgresql://sdp:x@127.0.0.1:1/postgres");
    expect(sdpEnvironment(linux, { url: "u", port: 1 }).SDP_DATA).toBeUndefined();
  });

  it("uses a database named in desktop-dev.json instead of its own", () => {
    expect(externalDatabase({})).toBeUndefined();
    expect(externalDatabase({ SDP_DATABASE_URL: "postgresql://postgres:pw@127.0.0.1:5544/sdp" })).toEqual({
      url: "postgresql://postgres:pw@127.0.0.1:5544/sdp",
      port: 5544,
    });
    expect(externalDatabase({ SDP_DATABASE_URL: "postgresql://localhost/sdp" })?.port).toBe(5432);
  });

  it("puts the venv first on PATH in the developer prompt's settings", () => {
    const text = commandPromptEnvironment({ SDP_DATA: "D:\\SDP" }, "D:\\code\\SDP\\python\\.venv\\Scripts");
    expect(text.split("\r\n")).toContain('set "PATH=D:\\code\\SDP\\python\\.venv\\Scripts;%PATH%"');
    const prompt = devPromptScript("D:\\SDP\\runtime\\sdp-env.cmd", "D:\\code\\100%SDP");
    expect(prompt).toContain('call "D:\\SDP\\runtime\\sdp-env.cmd"');
    expect(prompt).toContain('cd /d "D:\\code\\100%%SDP"');
  });

  it("reads desktop-dev.json, with or without a byte-order mark, and is empty without one", async () => {
    const dir = await mkdtemp(join(tmpdir(), "sdp-dev-"));
    expect(await readDevConfig(dir)).toEqual({ env: {} });
    await writeFile(join(dir, DEV_CONFIG), '\uFEFF{ "env": { "SDP_SIGNER": "leigh", "SDP_MIN_FREE_BYTES": 1024, "x": null } }');
    expect(await readDevConfig(dir)).toEqual({ env: { SDP_SIGNER: "leigh", SDP_MIN_FREE_BYTES: "1024" } });
  });

  it("says what a checkout is missing", async () => {
    const dir = await mkdtemp(join(tmpdir(), "sdp-checkout-"));
    expect(checkoutProblems(dir, false, "linux", "x64")[0]).toMatch(/not an SDP checkout/);
    await mkdir(join(dir, "python"), { recursive: true });
    await writeFile(join(dir, "python", "pyproject.toml"), "");
    expect(checkoutProblems(dir, false, "linux", "x64").join("\n")).toMatch(/uv sync[\s\S]*evidence-ui build[\s\S]*pnpm install/);
  });
});

describe("developer mode platforms", () => {
  it("names an unsupported platform instead of suggesting pnpm install", () => {
    expect(checkoutProblems("/x", false, "darwin", "arm64")[0]).toMatch(/Windows x64 and Linux x64, not darwin arm64/);
    expect(checkoutProblems("/x", false, "linux", "arm64")).toHaveLength(1);
    expect(checkoutProblems("/x", false, "linux", "x64")[0]).toMatch(/not an SDP checkout/);
  });
});
