/** ops-status — the maintenance-notice contract between the deploy operator and the UI.
 *
 *  The operator drops a JSON file on a path bind-mounted into this container (OPS_STATUS_FILE,
 *  default `/ops/status.json`) BEFORE touching infra and deletes it after. The file is the whole
 *  protocol: no redis, no backend hop — the notice must stay readable while agent-api/gateway are
 *  mid-restart, which is exactly when it matters. Absent/unreadable/malformed file = no notice. */
import { readFileSync } from "node:fs";

export type OpsStatus = {
  active: boolean;
  /** operator-written, user-facing one-liner ("Updating the agent service — chats may pause ~1 min") */
  message?: string;
  /** ISO 8601 start of the window, informational */
  since?: string;
};

export const OPS_STATUS_DEFAULT_FILE = "/ops/status.json";

export function readOpsStatus(file?: string): OpsStatus {
  const path = file || process.env.OPS_STATUS_FILE || OPS_STATUS_DEFAULT_FILE;
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (typeof raw !== "object" || raw === null || (raw as { active?: unknown }).active !== true) {
      return { active: false };
    }
    const { message, since } = raw as { message?: unknown; since?: unknown };
    return {
      active: true,
      message: typeof message === "string" && message.trim() ? message.trim() : undefined,
      since: typeof since === "string" ? since : undefined,
    };
  } catch {
    return { active: false };
  }
}
