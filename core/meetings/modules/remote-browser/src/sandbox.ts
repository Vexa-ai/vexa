/**
 * sandbox — what the browser is allowed to be: sandboxed wherever the host lets Chromium sandbox
 * itself, and given only the environment it needs to run.
 *
 * The bot drives pages it does not control. Chromium's sandbox (each renderer in its own user, PID
 * and network namespace, under seccomp-bpf) is what keeps a page that compromises its renderer from
 * reading the bot's files, its process environment or the stored browser session. So no launch-flag
 * set disables it any more: the launch keeps it on, and only drops it — saying why — where it
 * cannot start:
 *   • as root: Chromium will not sandbox a browser running as root;
 *   • where an unprivileged process may not create a user namespace: Docker's and containerd's
 *     default seccomp profiles refuse it (Lite's own profile allows it for meeting bots).
 *
 * The browser's environment is built from a short list (the display, the audio server, locale,
 * paths, proxies), never inherited whole: the bot's constructor (VEXA_BOT_CONFIG, with its tokens and
 * the session store's key) and anything else in the bot's environment stay out of every Chromium
 * process.
 */

/** The flags that turn Chromium's sandbox off — passed only when it cannot start (Playwright's own
 *  `chromiumSandbox: false` adds the first; they are passed explicitly so the launch says it). */
export const NO_SANDBOX_ARGS: readonly string[] = ['--no-sandbox', '--disable-setuid-sandbox'];

/** The environment keys a browser process is given, when the bot has them. */
export const BROWSER_ENV_KEYS: readonly string[] = [
  'PATH', 'HOME', 'TMPDIR', 'TZ', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE',
  'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'PULSE_SERVER', 'PULSE_SINK', 'PULSE_SOURCE',
  'FONTCONFIG_FILE', 'FONTCONFIG_PATH',
  'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
];

/** The browser's environment: only {@link BROWSER_ENV_KEYS}, taken from `env`. */
export function browserEnv(env: NodeJS.ProcessEnv = process.env): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of BROWSER_ENV_KEYS) {
    const value = env[key];
    if (typeof value === 'string') out[key] = value;
  }
  return out;
}

/** Why Chromium cannot sandbox itself here before even trying, or null if it may. */
export function sandboxRefusedUpFront(uid: number | undefined = process.getuid?.()): string | null {
  return uid === 0 ? 'the browser runs as root' : null;
}

/** Whether a failed launch is Chromium refusing to start because its sandbox cannot be set up. */
export function isSandboxStartFailure(err: unknown): boolean {
  const text = err instanceof Error ? `${err.message}\n${err.stack ?? ''}` : String(err);
  return /No usable sandbox/i.test(text);
}

export interface SandboxedLaunch<T> {
  result: T;
  /** Whether the browser runs with Chromium's sandbox on. */
  sandboxed: boolean;
  /** Why not, when it does not. */
  reason?: string;
}

/**
 * Launch with the sandbox on; without it only when it cannot start here (as root, or Chromium found
 * no usable sandbox), and then say so. `launch(sandbox)` launches with Chromium's sandbox on or off
 * (Playwright turns it off unless told otherwise, so the caller must pass `chromiumSandbox`). A
 * caller that put `--no-sandbox` in its own flags (a test harness) gets exactly that.
 */
export async function launchWithSandbox<T>(
  callerArgs: readonly string[],
  launch: (sandbox: boolean) => Promise<T>,
  opts: { uid?: number; log?: (line: string) => void } = {},
): Promise<SandboxedLaunch<T>> {
  const log = opts.log ?? ((line: string) => console.warn(line));
  if (callerArgs.includes('--no-sandbox')) {
    return { result: await launch(false), sandboxed: false, reason: 'the caller turned it off' };
  }
  const upFront = sandboxRefusedUpFront('uid' in opts ? opts.uid : process.getuid?.());
  if (upFront) {
    log(`[remote-browser] Chromium runs without its sandbox: ${upFront}`);
    return { result: await launch(false), sandboxed: false, reason: upFront };
  }
  try {
    const result = await launch(true);
    log('[remote-browser] Chromium runs with its sandbox');
    return { result, sandboxed: true };
  } catch (err) {
    if (!isSandboxStartFailure(err)) throw err;
    const reason = 'this host refuses it an unprivileged user namespace';
    log(`[remote-browser] Chromium runs without its sandbox: ${reason}`);
    return { result: await launch(false), sandboxed: false, reason };
  }
}
