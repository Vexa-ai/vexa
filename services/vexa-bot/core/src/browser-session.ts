import { chromium } from 'playwright-extra';
import { createClient, RedisClientType } from 'redis';
import { execSync } from 'child_process';
import { mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { getBrowserSessionArgs } from './constans';
import { BrowserSessionConfig } from './types';
import { TTSPlaybackService } from './services/tts-playback';
import { MeetingChatService } from './services/chat';
import { s3Sync, syncBrowserDataFromS3, syncBrowserDataToS3, cleanStaleLocks, BROWSER_DATA_DIR, BROWSER_CACHE_EXCLUDES } from './s3-sync';

const WORKSPACE_DIR = '/workspace';

// --- Git workspace helpers ---

function gitRepoUrl(config: BrowserSessionConfig): string {
  const repo = config.workspaceGitRepo!;
  const token = config.workspaceGitToken;
  if (!token) return repo;
  // Inject token into HTTPS URL: https://TOKEN@github.com/user/repo.git
  return repo.replace('https://', `https://${token}@`);
}

function syncWorkspaceFromGit(config: BrowserSessionConfig): void {
  const branch = config.workspaceGitBranch || 'main';
  const url = gitRepoUrl(config);
  console.log(`[browser-session] Git clone workspace from ${config.workspaceGitRepo} (${branch})`);
  try {
    if (existsSync(join(WORKSPACE_DIR, '.git'))) {
      // Already cloned — pull latest (ignore errors if remote branch doesn't exist yet)
      try {
        execSync(`git fetch origin && git reset --hard origin/${branch}`, { cwd: WORKSPACE_DIR, stdio: 'pipe', timeout: 60000 });
        console.log('[browser-session] Git pull complete');
      } catch {
        console.log('[browser-session] Git pull skipped (remote branch may not exist yet)');
      }
    } else {
      // Fresh clone — try with branch, fall back to bare clone, fall back to init
      try {
        execSync(`git clone --branch ${branch} "${url}" ${WORKSPACE_DIR}`, { stdio: 'pipe', timeout: 120000 });
      } catch {
        // Repo might be empty — clone without branch
        try {
          execSync(`git clone "${url}" ${WORKSPACE_DIR}`, { stdio: 'pipe', timeout: 120000 });
        } catch {
          // Truly empty repo or auth issue — init locally and set remote
          execSync('git init', { cwd: WORKSPACE_DIR, stdio: 'pipe' });
          execSync(`git remote add origin "${url}"`, { cwd: WORKSPACE_DIR, stdio: 'pipe' });
          console.log('[browser-session] Initialized empty workspace with remote');
        }
      }
      execSync('git config user.email "bot@vexa.ai"', { cwd: WORKSPACE_DIR, stdio: 'pipe' });
      execSync('git config user.name "Vexa Bot"', { cwd: WORKSPACE_DIR, stdio: 'pipe' });
      console.log('[browser-session] Git clone complete');
    }
  } catch (err: any) {
    console.log(`[browser-session] Git clone/pull failed: ${err.message}`);
  }
}

function syncWorkspaceToGit(config: BrowserSessionConfig): void {
  const branch = config.workspaceGitBranch || 'main';
  console.log(`[browser-session] Git push workspace to ${config.workspaceGitRepo}`);
  try {
    execSync('git add -A', { cwd: WORKSPACE_DIR, stdio: 'pipe' });
    const status = execSync('git status --porcelain', { cwd: WORKSPACE_DIR, encoding: 'utf8' }).trim();
    if (status) {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      execSync(`git commit -m "save ${timestamp}"`, { cwd: WORKSPACE_DIR, stdio: 'pipe' });
    }
    execSync(`git push origin ${branch}`, { cwd: WORKSPACE_DIR, stdio: 'pipe', timeout: 60000 });
    console.log('[browser-session] Git push complete');
  } catch (err: any) {
    console.log(`[browser-session] Git push failed: ${err.message}`);
  }
}

function useGitWorkspace(config: BrowserSessionConfig): boolean {
  return !!(config.workspaceGitRepo);
}

// --- Workspace sync (git or S3) ---

function syncWorkspaceDown(config: BrowserSessionConfig): void {
  if (useGitWorkspace(config)) {
    syncWorkspaceFromGit(config);
  } else {
    s3Sync(WORKSPACE_DIR, `${config.userdataS3Path}/workspace`, config, 'down');
  }
}

function syncWorkspaceUp(config: BrowserSessionConfig): void {
  if (useGitWorkspace(config)) {
    syncWorkspaceToGit(config);
  } else {
    s3Sync(WORKSPACE_DIR, `${config.userdataS3Path}/workspace`, config, 'up');
  }
}

function saveAll(config: BrowserSessionConfig): { success: boolean; error?: string } {
  try {
    console.log('[browser-session] Saving workspace...');
    syncWorkspaceUp(config);
  } catch (err: any) {
    console.error(`[browser-session] Workspace save failed: ${err.message}`);
    // Workspace failure is non-fatal, continue to browser data
  }
  try {
    console.log('[browser-session] Saving browser data...');
    syncBrowserDataToS3(config);
    console.log('[browser-session] Save complete');
    return { success: true };
  } catch (err: any) {
    console.error(`[browser-session] Browser data save FAILED: ${err.message}`);
    return { success: false, error: err.message };
  }
}

// --- Main entry point ---

export async function runBrowserSession(config: BrowserSessionConfig): Promise<void> {
  // Create directories
  mkdirSync(BROWSER_DATA_DIR, { recursive: true });
  mkdirSync(WORKSPACE_DIR, { recursive: true });

  // Download existing data
  syncBrowserDataFromS3(config);
  syncWorkspaceDown(config);

  // Clean stale locks
  cleanStaleLocks();

  // Launch persistent browser context
  const context = await chromium.launchPersistentContext(BROWSER_DATA_DIR, {
    headless: false,
    ignoreDefaultArgs: ['--enable-automation'],
    args: getBrowserSessionArgs(),
    viewport: null,
  });

  // Get or create a page
  const pages = context.pages();
  const page = pages.length > 0 ? pages[0] : await context.newPage();
  await page.goto('about:blank');

  console.log('[browser-session] Browser session ready. VNC :6080, CDP :9222');
  console.log(`[browser-session] Workspace: ${WORKSPACE_DIR}`);
  console.log(`[browser-session] Browser data: ${BROWSER_DATA_DIR}`);

  // Set up Redis subscriber for commands
  const channelName = `browser_session:${config.container_name || 'default'}`;

  if (config.redisUrl) {
    const subscriber: RedisClientType = createClient({ url: config.redisUrl }) as RedisClientType;
    const publisher: RedisClientType = createClient({ url: config.redisUrl }) as RedisClientType;
    await subscriber.connect();
    await publisher.connect();

    // Bug 1 fix: also subscribe to the bot_commands meeting channel so speak
    // commands published by meeting-api reach browser_session containers.
    const meetingChannelName = config.meeting_id
      ? `bot_commands:meeting:${config.meeting_id}`
      : null;

    // Bug 2 fix: initialise TTS service so speak commands can be handled.
    const ttsPlaybackService = new TTSPlaybackService();

    // Lazily initialised on the first chat_send command.
    let chatService: MeetingChatService | null = null;

    const getPlatformFromUrl = (url: string): string => {
      if (url.includes('meet.google.com')) return 'google_meet';
      if (url.includes('teams.microsoft.com') || url.includes('teams.live.com')) return 'teams';
      return 'unknown';
    };

    const handleCommand = async (message: string, channel: string) => {
      console.log(`[browser-session] Redis command on ${channel}: ${message}`);

      // Legacy plain-string commands (save_storage / stop)
      if (message === 'save_storage') {
        const result = saveAll(config);
        if (result.success) {
          await publisher.publish(channelName, 'save_storage:done');
        } else {
          await publisher.publish(channelName, `save_storage:error:${result.error}`);
        }
        return;
      } else if (message === 'stop') {
        console.log('[browser-session] Stop command received, saving and exiting...');
        saveAll(config);
        await context.close();
        process.exit(0);
        return;
      }

      // JSON-encoded commands (speak, speak_audio, speak_stop, leave, …)
      let command: any;
      try {
        command = JSON.parse(message);
      } catch {
        console.log(`[browser-session] Unrecognised non-JSON command: ${message}`);
        return;
      }

      if (command.action === 'speak') {
        console.log(`[browser-session] Speak command: "${(command.text || '').substring(0, 50)}"`);
        try {
          const provider = command.provider || process.env.DEFAULT_TTS_PROVIDER || 'openai';
          const voice = command.voice || process.env.DEFAULT_TTS_VOICE || 'alloy';
          await ttsPlaybackService.synthesizeAndPlay(command.text, provider, voice);
        } catch (err: any) {
          console.log(`[browser-session] TTS speak failed: ${err.message}`);
        }
      } else if (command.action === 'speak_audio') {
        console.log('[browser-session] Speak audio command');
        try {
          if (command.audio_url) {
            await ttsPlaybackService.playFromUrl(command.audio_url);
          } else if (command.audio_base64) {
            const format = command.format || 'wav';
            const sampleRate = command.sample_rate || 24000;
            await ttsPlaybackService.playFromBase64(command.audio_base64, format, sampleRate);
          }
        } catch (err: any) {
          console.log(`[browser-session] TTS speak_audio failed: ${err.message}`);
        }
      } else if (command.action === 'speak_stop') {
        console.log('[browser-session] Speak stop command');
        ttsPlaybackService.interrupt();
      } else if (command.action === 'leave') {
        console.log('[browser-session] Leave command received, saving and exiting...');
        saveAll(config);
        await context.close();
        process.exit(0);
      } else if (command.action === 'chat_send') {
        console.log(`[browser-session] Processing chat_send command: "${(command.text || '').substring(0, 50)}..."`);
        try {
          const currentPage = context.pages()[0];
          if (!currentPage) {
            console.log('[browser-session] [Chat] No page available for chat_send');
          } else {
            // Lazily initialise MeetingChatService on first use, or re-init if page changed.
            if (!chatService) {
              const platform = getPlatformFromUrl(currentPage.url());
              const meetingId = config.meeting_id ?? 0;
              const botName = 'Vexa Bot';
              console.log(`[browser-session] [Chat] Initialising MeetingChatService (platform=${platform}, meetingId=${meetingId})`);
              chatService = new MeetingChatService(currentPage, platform, meetingId, botName, config.redisUrl);
            }
            const success = await chatService.sendMessage(command.text || '');
            if (success) {
              await publisher.publish(
                `bot_commands:meeting:${config.meeting_id}`,
                JSON.stringify({ action: 'chat.sent', text: command.text })
              );
            } else {
              console.log('[browser-session] [Chat] sendMessage returned false');
            }
          }
        } catch (err: any) {
          console.log(`[browser-session] [Chat] chat_send failed: ${err.message}`);
        }
      } else {
        console.log(`[browser-session] Unhandled command action: ${command.action}`);
      }
    };

    await subscriber.subscribe(channelName, (message: string) => handleCommand(message, channelName));

    if (meetingChannelName) {
      await subscriber.subscribe(meetingChannelName, (message: string) => handleCommand(message, meetingChannelName));
      console.log(`[browser-session] Listening for commands on Redis channels: ${channelName}, ${meetingChannelName}`);
    } else {
      console.log(`[browser-session] Listening for commands on Redis channel: ${channelName}`);
    }
  }

  // Graceful shutdown
  const shutdown = async () => {
    console.log('[browser-session] Shutting down, saving...');
    saveAll(config);
    await context.close();
    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  // Auto-save browser data every 60s — ensures login state persists
  // even if the container is killed without graceful shutdown
  const autoSaveInterval = setInterval(() => {
    try {
      syncBrowserDataToS3(config);
    } catch (err: any) {
      console.error(`[browser-session] Auto-save failed: ${err.message}`);
    }
  }, 60_000);

  // Keep alive
  await new Promise(() => {});
}
