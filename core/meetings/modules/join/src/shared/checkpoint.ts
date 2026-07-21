import { mkdirSync } from "fs";
import { join } from "path";
import { Page } from "playwright";
import { log } from "../_host";

/** Diagnostic checkpoint screenshots — ALWAYS best-effort.
 *
 *  The hosted bot runs with a read-only root filesystem (restricted PSA): the
 *  historical hardcoded `/app/storage/screenshots/…` writes threw EROFS on the
 *  very first post-navigation checkpoint, which the orchestrator classified as
 *  a join_failure — every hosted join died in ~1s, silently, for a DEBUG PNG.
 *  A diagnostics write must never decide a join. The directory is env-tunable
 *  (BOT_SCREENSHOT_DIR), defaults under /tmp (the pod's writable emptyDir),
 *  and every failure — mkdir or capture — logs and moves on. */
let resolvedDir: string | null | undefined;

export async function checkpoint(page: Page, label: string): Promise<void> {
  if (resolvedDir === undefined) {
    const dir = process.env.BOT_SCREENSHOT_DIR ?? "/tmp/screenshots";
    try {
      mkdirSync(dir, { recursive: true });
      resolvedDir = dir;
    } catch (e) {
      log(`checkpoint screenshots disabled (cannot create ${dir}: ${String(e)})`);
      resolvedDir = null;
    }
  }
  if (resolvedDir === null) return;
  try {
    await page.screenshot({ path: join(resolvedDir, `bot-checkpoint-${label}.png`), fullPage: true });
    log(`📸 checkpoint: ${label}`);
  } catch (e) {
    log(`checkpoint '${label}' skipped: ${String(e)}`);
  }
}

/** Test seam: forget the cached directory verdict. */
export function resetCheckpointDirForTest(): void {
  resolvedDir = undefined;
}
