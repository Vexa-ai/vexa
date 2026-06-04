// Clean-room OS-level input layer (Apache-2.0).
//
// Drives real X11 input through `xdotool`, which issues events via the XTEST
// extension. Unlike Playwright/CDP synthetic input, XTEST events are delivered
// by the X server itself, so the browser sees `isTrusted: true` pointer/keyboard
// events with genuine MotionNotify — the property Google Meet's bot-detection
// keys on. Runs against the bot's headful Chrome on DISPLAY (:99 by default).

import { execFile } from "child_process";

export interface PointerLocation {
  x: number;
  y: number;
}

export interface X11InputOptions {
  display?: string;
  /** When true, commands are recorded instead of executed (unit tests). */
  dryRun?: boolean;
}

export class X11Input {
  private display: string;
  private dryRun: boolean;
  /** Recorded argv lines when dryRun is on. */
  readonly log: string[][] = [];
  /** Simulated pointer position, maintained only in dryRun for faithful tests. */
  private simPointer: PointerLocation = { x: 0, y: 0 };

  constructor(opts: X11InputOptions = {}) {
    this.display = opts.display ?? process.env.DISPLAY ?? ":99";
    this.dryRun = opts.dryRun ?? false;
  }

  private run(args: string[], stdin?: Buffer): Promise<string> {
    if (this.dryRun) {
      this.log.push(args);
      return Promise.resolve("");
    }
    return new Promise((resolve, reject) => {
      const child = execFile(
        args[0],
        args.slice(1),
        { env: { ...process.env, DISPLAY: this.display } },
        (err, stdout) => {
          if (err) reject(err);
          else resolve(stdout);
        }
      );
      if (stdin && child.stdin) {
        child.stdin.write(stdin);
        child.stdin.end();
      }
    });
  }

  /** Verify xdotool is installed and the X display is reachable. */
  async isAvailable(): Promise<boolean> {
    if (this.dryRun) return true;
    try {
      await this.run(["xdotool", "getdisplaygeometry"]);
      return true;
    } catch {
      return false;
    }
  }

  async getPointer(): Promise<PointerLocation> {
    if (this.dryRun) return { ...this.simPointer };
    const out = await this.run(["xdotool", "getmouselocation", "--shell"]);
    const x = Number(/X=(-?\d+)/.exec(out)?.[1] ?? 0);
    const y = Number(/Y=(-?\d+)/.exec(out)?.[1] ?? 0);
    return { x, y };
  }

  async moveAbs(x: number, y: number): Promise<void> {
    if (this.dryRun) this.simPointer = { x, y };
    await this.run(["xdotool", "mousemove", "--sync", String(x), String(y)]);
  }

  async moveRel(dx: number, dy: number): Promise<void> {
    if (this.dryRun) this.simPointer = { x: this.simPointer.x + dx, y: this.simPointer.y + dy };
    await this.run(["xdotool", "mousemove_relative", "--sync", "--", String(dx), String(dy)]);
  }

  async buttonDown(button = 1): Promise<void> {
    await this.run(["xdotool", "mousedown", String(button)]);
  }

  async buttonUp(button = 1): Promise<void> {
    await this.run(["xdotool", "mouseup", String(button)]);
  }

  async key(keyName: string): Promise<void> {
    await this.run(["xdotool", "key", "--clearmodifiers", keyName]);
  }

  /**
   * Put text on the clipboard (xclip) and paste with Ctrl+V.
   * WARNING: under execFile this can hang — `xclip` keeps running to serve the
   * X selection and never exits. Prefer `typeText`. Kept for completeness/tests.
   */
  async clipboardPaste(text: string): Promise<void> {
    await this.run(["xclip", "-selection", "clipboard"], Buffer.from(text, "utf-8"));
    await this.key("ctrl+v");
  }

  /** Type literal text via XTEST keystrokes with a per-char delay (ms). */
  async typeText(text: string, delayMs = 60): Promise<void> {
    await this.run([
      "xdotool",
      "type",
      "--clearmodifiers",
      "--delay",
      String(delayMs),
      "--",
      text,
    ]);
  }
}
