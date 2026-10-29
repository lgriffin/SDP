// Smoke test of the staged resources without Electron: a fresh data folder, the whole start-up, a page
// from the UI that came through sdp-api, then a clean shutdown. CI runs it on Windows before packaging.
//   node dist/smoke.js [resources-dir]
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { type Backend, startBackend } from "./backend.ts";

const resources = resolve(process.argv[2] ?? join(import.meta.dirname, "..", "resources", "sdp"));
const data = await mkdtemp(join(tmpdir(), "sdp-desktop-smoke-"));
const layout = { resources, data, windows: process.platform === "win32" };

let failed = false;
let backend: Backend | undefined;
try {
  backend = await startBackend(layout, (step) => console.log(`· ${step}`));
  const health = await fetch(`${backend.apiUrl}/health`);
  console.log(`sdp-api /health: ${health.status} ${await health.text()}`);
  if (!health.ok) throw new Error("sdp-api reports itself unhealthy");
  const query = JSON.stringify({ query: "{ experiments { experimentId } }" });
  const anonymous = await fetch(`${backend.apiUrl}/graphql`, { method: "POST", body: query, headers: { "content-type": "application/json" } });
  if (anonymous.status !== 401) throw new Error(`sdp-api answered a request without its token with ${anonymous.status}`);
  const page = await fetch(backend.uiUrl);
  const html = await page.text();
  console.log(`UI ${backend.uiUrl}: ${page.status}, ${html.length} bytes`);
  if (page.status !== 200 || !html.includes("<html")) throw new Error("the UI did not serve its home page");
} catch (error) {
  failed = true;
  console.error(error);
} finally {
  await backend?.stop();
  if (failed) console.error(`logs kept in ${join(data, "logs")}`);
  else await rm(data, { recursive: true, force: true });
}
console.log(failed ? "smoke test failed" : "smoke test passed");
process.exit(failed ? 1 : 0);
