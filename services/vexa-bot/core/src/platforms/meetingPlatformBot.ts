import { Page } from "playwright";
import { BotConfig } from "../types";

/**
 * Abstract base class for meeting platform bots.
 * Extend this class to implement support for a new platform.
 */
export abstract class MeetingPlatformBase {
  protected page: Page;
  protected botConfig: BotConfig;

  constructor(page: Page, botConfig: BotConfig) {
    this.page = page;
    this.botConfig = botConfig;
  }

  /**
   * Main entry point to handle the meeting lifecycle.
   * This should orchestrate join, admission, recording, and leave logic.
   */
  abstract handleMeeting(
    gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string) => Promise<void>
  ): Promise<void>;

  /**
   * Join the meeting. Override this to customize join logic for a platform.
   */
  abstract joinMeeting(): Promise<void>;

  /**
   * Wait for admission to the meeting (e.g., waiting room).
   * Can be overridden for platform-specific admission logic.
   */
  abstract waitForAdmission(): Promise<boolean>;

  /**
   * Prepare for recording (e.g., expose functions to browser context).
   * Override if platform needs special setup.
   */
  abstract prepareForRecording(): Promise<void>;

  /**
   * Start the actual recording process.
   * Override for platform-specific recording logic.
   */
  abstract startRecording(): Promise<void>;

  /**
   * Leave the meeting gracefully.
   * Override for platform-specific leave logic.
   */
  abstract leaveMeeting(): Promise<boolean>;
}
