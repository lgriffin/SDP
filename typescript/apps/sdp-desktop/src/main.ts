// Electron main process: one window showing the evidence UI, with the backend started behind it and
// stopped when the window closes. Pages from anywhere but the local UI open in the default browser.
import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { app, BrowserWindow, dialog, Menu, shell } from "electron";

import { type Backend, startBackend } from "./backend.ts";
import { checkoutOf, checkoutProblems, DEV_DATA_NAME, parseDevOptions, readDevConfig } from "./dev.ts";
import { defaultDataDir, type Layout, logs, runtime } from "./layout.ts";

const windows = process.platform === "win32";
const options = parseDevOptions(process.argv, process.env);
// Developer mode runs from the checkout this file sits in; an installed app never does.
const dev = options.dev && !app.isPackaged;
const checkout = dev ? checkoutOf(import.meta.dirname) : undefined;
const defaultData = defaultDataDir(process.env, homedir(), windows);
const layout: Layout = {
  resources: app.isPackaged ? join(process.resourcesPath, "sdp") : join(import.meta.dirname, "..", "resources", "sdp"),
  data: resolve(
    options.data ?? process.env.SDP_DESKTOP_DATA ?? (dev ? join(dirname(defaultData), windows ? DEV_DATA_NAME : DEV_DATA_NAME.toLowerCase()) : defaultData),
  ),
  windows,
  checkout,
};

if (dev) {
  // Its own name and Electron profile, so it runs beside the installed app (the single-instance lock is
  // per profile) and the two never share a window or a cache.
  app.setName("SDP (dev)");
  app.setPath("userData", join(layout.data, "electron"));
}

let backend: Backend | undefined;
let starting: Promise<Backend> | undefined;
let window: BrowserWindow | undefined;
let quitting = false;

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 520,
    title: dev ? "SDP (dev)" : "SDP",
    show: false,
    autoHideMenuBar: !dev,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (backend && url.startsWith(backend.uiUrl)) return;
    if (url.startsWith("file:")) return;
    event.preventDefault();
    if (/^https?:/.test(url)) void shell.openExternal(url);
  });
  return win;
}

/** The Developer menu: a prompt on the checkout, folders, archive registration and updating. */
function developerMenu(): void {
  if (!checkout) return;
  const updateScript = join(checkout, "setup-dev.ps1");
  const template: Electron.MenuItemConstructorOptions[] = [
    { role: "fileMenu" },
    { role: "editMenu" },
    { role: "viewMenu" },
    {
      label: "Developer",
      submenu: [
        {
          label: "Open the developer prompt",
          accelerator: "CmdOrCtrl+Shift+P",
          enabled: windows,
          click: () => {
            // A detached cmd gets a console window of its own.
            spawn("cmd.exe", ["/k", join(runtime(layout), "sdp-dev-prompt.cmd")], { detached: true, stdio: "ignore", cwd: checkout }).unref();
          },
        },
        { label: "Open the checkout folder", click: () => void shell.openPath(checkout) },
        { label: "Open the data folder", click: () => void shell.openPath(layout.data) },
        { label: "Open the log folder", click: () => void shell.openPath(logs(layout)) },
        { type: "separator" },
        {
          label: "Register the archives again",
          click: async () => {
            if (!backend) return;
            try {
              await backend.registerArchives();
              await dialog.showMessageBox({ type: "info", message: "The archives are registered." });
            } catch (error) {
              await dialog.showMessageBox({
                type: "error",
                message: "Registering the archives failed.",
                detail: `${error instanceof Error ? error.message : String(error)}\n\nSee integration.log in ${logs(layout)}.`,
              });
            }
          },
        },
        {
          label: "Update and restart…",
          enabled: windows,
          click: async () => {
            const { response } = await dialog.showMessageBox({
              type: "question",
              message: "Update the checkout and restart?",
              detail:
                "SDP (dev) closes, then setup-dev.ps1 pulls the current branch (only if it has no uncommitted changes), " +
                "runs uv sync, pnpm install and the UI build, and opens SDP (dev) again. Its window shows each step.",
              buttons: ["Update and restart", "Cancel"],
              defaultId: 0,
              cancelId: 1,
            });
            if (response !== 0) return;
            const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", updateScript];
            args.push("-Checkout", checkout, "-Data", layout.data, "-Update", "-Launch", "-WaitForPid", String(process.pid));
            spawn("powershell.exe", args, { detached: true, stdio: "ignore", cwd: checkout }).unref();
            app.quit();
          },
        },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function launch(): Promise<void> {
  window = createWindow();
  await window.loadFile(join(import.meta.dirname, "..", "static", "starting.html"));
  try {
    if (checkout) {
      const problems = checkoutProblems(checkout, windows);
      if (problems.length) throw new Error(`${problems.join("\n")}\n\nsetup-dev.ps1 in ${checkout} does all of this.`);
    }
    const config = dev ? await readDevConfig(layout.data) : { env: {} };
    starting = startBackend(
      layout,
      (step) => {
        if (!window || window.isDestroyed()) return;
        void window.webContents.executeJavaScript(`window.sdpStep && window.sdpStep(${JSON.stringify(step)})`).catch(() => {});
      },
      config,
    );
    backend = await starting;
    if (quitting) return;
    await window.loadURL(backend.uiUrl);
  } catch (error) {
    if (quitting) return;
    const { response } = await dialog.showMessageBox(window, {
      type: "error",
      title: "SDP could not start",
      message: "SDP could not start.",
      detail: `${error instanceof Error ? error.message : String(error)}\n\nThe log files in ${logs(layout)} say more. Send them to whoever looks after SDP for you.`,
      buttons: ["Open the log folder", "Close"],
      defaultId: 0,
    });
    if (response === 0) await shell.openPath(logs(layout));
    app.quit();
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (window?.isMinimized()) window.restore();
    window?.focus();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (quitting || !starting) return;
    // Stop sdp-api and shut PostgreSQL down cleanly before the process ends. Closing during start-up
    // waits for it to settle: a failed start-up has already stopped what it started.
    event.preventDefault();
    quitting = true;
    void starting
      .then((b) => b.stop())
      .catch(() => {})
      .finally(() => app.quit());
  });
  // A terminal's Ctrl+C or a service manager's stop takes the same clean path as closing the window.
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => app.quit());
  void app.whenReady().then(() => {
    developerMenu();
    return launch();
  });
}
