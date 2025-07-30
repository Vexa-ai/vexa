import { GoogleMeetBot } from "./google";
import { BotConfig } from "../types";
import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { authenticator } from 'otplib';

export class GoogleOktaBot extends GoogleMeetBot {
    botConfig: BotConfig;
    page: Page;

    constructor(page: Page, botConfig: BotConfig) {
        super(page, botConfig);
        this.botConfig = botConfig;
        this.page = page;
    }

    async _handle2FAChallenge(): Promise<void> {

        const PASSCODE_SELECTOR = 'input[name="credentials.passcode"]';
        const SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]';
    

        log('Detecting 2FA challenge...');

        // Wait for 2FA input field to appear
        try {
            await this.page.waitForSelector(PASSCODE_SELECTOR, {
                state: 'visible',
                timeout: 2000,
            });

        log('2FA challenge detected, generating TOTP code...');
        if (!this.botConfig.credentials || !this.botConfig.credentials.mfaSecret) {
            log('mfaSecret is not set in botConfig.credentials, skipping 2FA challenge handling.');
            return;
        }
        const totpCode = authenticator.generate(this.botConfig.credentials.mfaSecret);

            if (totpCode) {
            // Fill in the TOTP code
            await this.page.fill(PASSCODE_SELECTOR, totpCode);

            // Click submit button
            await this.page.click(SUBMIT_SELECTOR);

            // Wait for navigation or success
            await this.page.waitForNavigation({
                waitUntil: 'networkidle',
                timeout: 20000,
            });
            log('2FA challenge completed successfully.');
            }
        } catch (error) {
            log('No 2FA challenge detected.');
        }
    }

    async _loginWithOkta(): Promise<void> {
        if (!this.botConfig.credentials || !this.botConfig.credentials.googleUsername || !this.botConfig.credentials.googlePassword) {
            log(JSON.stringify(this.botConfig));
            throw new Error("Google credentials are not set in botConfig.credentials.");
        }

        log('[INFO] Navigating to Google login...');
        await this.page.goto('https://accounts.google.com/signin', {
            waitUntil: 'networkidle',
        });

        // We first fill in the email on google. Once this is done we are
        // redirected to okta where we need to fill in the username
        await this.page.fill('input[type="email"]', this.botConfig.credentials.googleUsername);
        await this.page.click('#identifierNext');

        await this.page.waitForSelector('input[type="text"][name="identifier"]', {
            state: 'visible',
            timeout: 15000,
        });
        await this.page.fill('input[type="text"][name="identifier"]', this.botConfig.credentials.googleUsername);
        await this.page.click('input[type="submit"]');

        await this.page.waitForSelector('input[type="password"]', {
            state: 'visible',
            timeout: 15000,
        });
        await this.page.fill('input[type="password"]', this.botConfig.credentials.googlePassword);
        await this.page.click('input[type="submit"]');

        // Wait for navigation or 2FA challenge
        try {
            await this.page.waitForNavigation({ waitUntil: 'networkidle', timeout: 20000 });
        } catch (error) {
            // If navigation times out, check for 2FA challenge
            log('[INFO] Navigation timeout, checking for 2FA challenge...');
        }

        // Handle 2FA if MFA_SECRET is provided
        if (this.botConfig.credentials && this.botConfig.credentials.mfaSecret) {
            await this._handle2FAChallenge();
        }

        log('Google login successful.');
    }


    async joinMeeting(): Promise<void> {

        log("Logging in to Google with Okta...");
        await this._loginWithOkta();

        const CONTINUE_WITHOUT_SELECTOR = 'span:has-text("Continue without microphone and camera")';
        const JOIN_NOW_SELECTOR = 'span:has-text("Join now")';

        if (!this.botConfig.meetingUrl) {
            throw new Error("Meeting URL is not provided in botConfig.");
        }

        await this.page.goto(this.botConfig.meetingUrl, { waitUntil: 'networkidle' });

        // Look for and click "Continue without microphone and camera" button
        try {
            await this.page.waitForSelector(
                CONTINUE_WITHOUT_SELECTOR,
                { state: 'visible', timeout: 10000 }
            );
            await this.page.click(CONTINUE_WITHOUT_SELECTOR);
            log(
            'Clicked "Continue without microphone and camera" button.'
            );
        } catch (e) {
            log(
            '"Continue without microphone and camera" button not found or already handled.'
            );
        }

        // Wait for the join button and click it
        try {
            await this.page.waitForSelector(JOIN_NOW_SELECTOR, {
            state: 'visible',
            timeout: 20000,
            });
            await this.page.click(JOIN_NOW_SELECTOR);
            log('[INFO] Joined the Google Meet.');
        } catch (e) {
            throw new Error(
            'Join button not found or could not be clicked. Ensure you are on the correct page and the meeting is accessible.'
            );
        }
    }
}

let _GoogleMeetBot: GoogleOktaBot | null = null;

export function handleGoogleOkta(
  botConfig: BotConfig,
  page: any,
  performGracefulLeave: (page: any, exitCode?: number, reason?: string) => Promise<void>
) {
  _GoogleMeetBot = new GoogleOktaBot(page, botConfig);
  return _GoogleMeetBot.handleMeeting(performGracefulLeave);
}

export function leaveGoogleOkta(page: any): Promise<boolean> {
  if (!_GoogleMeetBot) {
    return Promise.reject(new Error("Google Okta bot not initialized."));
  }
  return _GoogleMeetBot.leaveMeeting();
}
