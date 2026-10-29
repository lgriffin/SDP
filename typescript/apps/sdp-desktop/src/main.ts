// Electron main process: one window showing the evidence UI, with the backend started behind it and
// stopped when the window closes. Pages from anywhere but the local UI open in the default browser.
import { homedir } from "node:os";
import { join } from "node:path";

import { app, BrowserWindow, dialog, shell } from "electron";

import { type Backend, startBackend } from "./backend.ts";
import { defaultDataDir, type Layout, logs } from "./layout.ts";

const windows = process.platform === "win32";
const layout: Layout = {
  resources: app.isPackaged ? join(process.resourcesPath, "sdp") : join(import.meta.dirname, "..", "resources", "sdp"),
  data: process.env.SDP_DESKTOP_DATA ?? defaultDataDir(process.env, homedir(), windows),
  windows,
};

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
    title: "SDP",
    show: false,
    autoHideMenuBar: true,
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

async function launch(): Promise<void> {
  window = createWindow();
  await window.loadFile(join(import.meta.dirname, "..", "static", "starting.html"));
  try {
    starting = startBackend(layout, (step) => {
      if (!window || window.isDestroyed()) return;
      void window.webContents.executeJavaScript(`window.sdpStep && window.sdpStep(${JSON.stringify(step)})`).catch(() => {});
    });
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
  void app.whenReady().then(launch);
}
