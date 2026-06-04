import { runBot } from "."
import { z } from 'zod';
import { BotConfig, BrowserSessionConfig } from "./types";

// Browser session mode schema — only needs Redis + S3/workspace config
const BrowserSessionConfigSchema = z.object({
  mode: z.literal("browser_session"),
  meeting_id: z.number().int().optional(),
  redisUrl: z.string(),
  container_name: z.string().optional(),
  meetingApiCallbackUrl: z.string().url().optional(),
  s3Endpoint: z.string().optional(),
  s3Bucket: z.string().optional(),
  s3AccessKey: z.string().optional(),
  s3SecretKey: z.string().optional(),
  userdataS3Path: z.string().optional(),
  workspaceGitRepo: z.string().optional(),
  workspaceGitToken: z.string().optional(),
  workspaceGitBranch: z.string().optional(),
});

// Meeting mode schema — requires platform, meetingUrl, botName
export const BotConfigSchema = z.object({
  mode: z.enum(["meeting", "browser_session"]).default("meeting"),
  platform: z.enum(["google_meet", "zoom", "teams"]),
  meetingUrl: z.string().url().nullable(),
  botName: z.string(),
  token: z.string().optional(),
  connectionId: z.string().optional(),
  nativeMeetingId: z.string().optional(),
  language: z.string().nullish(),
  task: z.string().nullish(),
  allowedLanguages: z.array(z.string()).optional(),
  transcribeEnabled: z.boolean().optional(),
  transcriptionTier: z.enum(["realtime", "deferred"]).optional(),
  redisUrl: z.string(),
  container_name: z.string().optional(),
  automaticLeave: z.object({
    waitingRoomTimeout: z.number().int().default(300000),
    noOneJoinedTimeout: z.number().int().default(600000),
    everyoneLeftTimeout: z.number().int().default(120000)
  }).default({}),
  reconnectionIntervalMs: z.number().int().optional(),
  meeting_id: z.number().int().optional(),
  meetingApiCallbackUrl: z.string().url().optional(),
  recordingEnabled: z.boolean().optional(),
  captureModes: z.array(z.string()).optional(),
  recordingUploadUrl: z.string().url().optional(),
  transcriptionServiceUrl: z.string().optional(),
  transcriptionServiceToken: z.string().optional(),
  voiceAgentEnabled: z.boolean().optional(),
  defaultAvatarUrl: z.string().url().optional(),
  videoReceiveEnabled: z.boolean().optional(),
  cameraEnabled: z.boolean().optional(),
  uiInteractionMode: z.enum(["humanized", "synthetic"]).optional(),
  authenticated: z.boolean().optional(),
  userdataS3Path: z.string().optional(),
  s3Endpoint: z.string().optional(),
  s3Bucket: z.string().optional(),
  s3AccessKey: z.string().optional(),
  s3SecretKey: z.string().optional(),
  workspaceGitRepo: z.string().optional(),
  workspaceGitToken: z.string().optional(),
  workspaceGitBranch: z.string().optional(),
});


// #407 407-C: a crash BEFORE runBot (missing/invalid BOT_CONFIG, schema-validation
// throw, module-import failure) otherwise leaves meetings.data.bot_logs EMPTY — a silent
// "zero-log" failure the operator can't diagnose (3 such crashes seen post-hotfix). Two
// guards: (1) a structured startup breadcrumb on the very first line, and (2) best-effort
// report the failure reason to meeting-api so the crash is RECORDED, never invisible.
function startupBreadcrumb(): void {
  try {
    console.log(JSON.stringify({
      ts: new Date().toISOString(), level: "info", subsystem: "startup",
      msg: "bot container starting (docker.ts main, pre-config)",
    }));
  } catch { /* never let the breadcrumb throw */ }
}

async function reportStartupFailure(rawConfig: string | undefined, error: any): Promise<void> {
  try {
    let cfg: any = {};
    if (rawConfig) { try { cfg = JSON.parse(rawConfig); } catch { /* not even JSON */ } }
    const url = cfg?.meetingApiCallbackUrl;
    const connectionId = cfg?.connectionId;
    if (!url || !connectionId) return; // can't reach meeting-api without these — nothing to do
    const endpoint = String(url).replace("/exited", "/status_change");
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 5000);
    await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        connection_id: connectionId,
        container_id: cfg?.container_name,
        status: "failed",
        reason: "startup_failure",
        failure_stage: "requested",
        completion_reason: "validation_error",
        error_details: { message: String(error?.message ?? error), where: "docker.ts:main (pre-runBot)" },
        bot_logs: [`[startup] FATAL pre-runBot failure: ${String(error?.message ?? error)}`],
        timestamp: new Date().toISOString(),
      }),
      signal: controller.signal,
    }).catch(() => { /* swallow — best-effort */ });
    clearTimeout(t);
  } catch { /* the reporter must never throw */ }
}

(async function main() {
  startupBreadcrumb();
  const rawConfig = process.env.BOT_CONFIG;
  if (!rawConfig) {
    console.error("BOT_CONFIG environment variable is not set");
    await reportStartupFailure(rawConfig, new Error("BOT_CONFIG environment variable is not set"));
    process.exit(1);
  }

  try {
    const parsedConfig = JSON.parse(rawConfig);

    // Check mode BEFORE Zod validation — each mode has its own schema
    if (parsedConfig.mode === "browser_session") {
      const sessionConfig = BrowserSessionConfigSchema.parse(parsedConfig);
      import('./browser-session').then(({ runBrowserSession }) => {
        runBrowserSession(sessionConfig).catch((error) => {
          console.error("Error running browser session:", error);
          process.exit(1);
        });
      });
    } else {
      const validatedConfig = BotConfigSchema.parse(parsedConfig);
      const botConfig: BotConfig = validatedConfig as BotConfig;
      runBot(botConfig).catch((error) => {
        console.error("Error running bot:", error);
        process.exit(1);
      });
    }
  } catch (error) {
    console.error("Invalid BOT_CONFIG:", error);
    await reportStartupFailure(rawConfig, error);  // record the reason in the DB, not a silent zero-log crash
    process.exit(1);
  }
})()
