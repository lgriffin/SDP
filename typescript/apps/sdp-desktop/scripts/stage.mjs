// `pnpm stage`: runs scripts/stage.sh with the right bash whichever `bash` comes first on PATH (#194).
// On Windows a bare `bash` is often WSL's (C:\Windows\System32 or WindowsApps\bash.exe), which would
// stage Linux binaries or fail on `node: not found`; this finds Git Bash instead.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

/** Git Bash candidates, most likely first: next to git.exe, then the standard install folders. */
export function gitBashCandidates(env = process.env) {
  const out = [];
  const git = spawnSync("git", ["--exec-path"], { encoding: "utf8" });
  if (git.status === 0 && git.stdout.trim()) {
    // <Git>\mingw64\libexec\git-core -> <Git>\bin\bash.exe
    out.push(resolve(git.stdout.trim(), "..", "..", "..", "bin", "bash.exe"));
  }
  for (const root of [env.ProgramFiles, env["ProgramFiles(x86)"], env.LOCALAPPDATA && join(env.LOCALAPPDATA, "Programs")]) {
    if (root) out.push(join(root, "Git", "bin", "bash.exe"));
  }
  return out;
}

function bash() {
  if (process.platform !== "win32") return "bash";
  if (process.env.SDP_BASH) return process.env.SDP_BASH;
  const found = gitBashCandidates().find((p) => existsSync(p));
  if (!found) {
    console.error(
      "stage: Git Bash not found. Install Git for Windows (https://git-scm.com/download/win), " +
        "or set SDP_BASH to the full path of its bin\\bash.exe. WSL's bash will not do: it stages Linux binaries.",
    );
    process.exit(1);
  }
  return found;
}

const shell = bash();
console.log(`stage: running scripts/stage.sh with ${shell}`);
const result = spawnSync(shell, [join(here, "stage.sh")], { stdio: "inherit" });
if (result.error) {
  console.error(`stage: could not start ${shell}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
