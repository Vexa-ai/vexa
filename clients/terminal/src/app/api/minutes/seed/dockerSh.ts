/** Run `sh -c <script> _ <args…>` inside a container, feeding `stdin` to it — R-E12's seam.
 *
 *  A separate module for one reason: it is what the seed route's test replaces. Keeping the
 *  process spawn inside the route made the only proof available a source grep, which is exactly
 *  the class of test that let the injection this fixes ship in the first place.
 *
 *  Nothing caller-controlled goes into `script`. Paths are `args` — `"$1"`, `"$2"` inside the
 *  script — and file content is `stdin`, so neither can be read as shell syntax.
 */
import { spawn } from "child_process";

export function dockerSh(container: string, script: string, args: string[], stdin: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const p = spawn("docker", ["exec", "-i", container, "sh", "-c", script, "_", ...args], {
      timeout: 15000,
    });
    let err = "";
    p.stderr?.on("data", (d) => { err += String(d); });
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(err.slice(0, 300) || `exit ${code}`))));
    p.stdin?.on("error", () => { /* the child may exit before we finish writing */ });
    p.stdin?.end(stdin);
  });
}
