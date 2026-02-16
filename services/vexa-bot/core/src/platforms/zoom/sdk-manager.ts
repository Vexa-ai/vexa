import { BotConfig } from '../../types';
import * as crypto from 'crypto';

// Load native addon (built to /vexa/services/vexa-bot/build/Release/)
let addon: any = null;
let addonLoadError: unknown = null;

try {
  addon = require('../../../../build/Release/zoom_sdk_wrapper');
} catch (error) {
  addonLoadError = error;
  console.warn('[Zoom SDK] Native addon not found. Running in stub mode.');
}

export class ZoomSDKManager {
  private sdk: any;
  private config: BotConfig;
  private isStubMode: boolean = false;

  constructor(config: BotConfig) {
    this.config = config;

    if (!addon) {
      this.isStubMode = true;
      console.warn('[Zoom SDK] Operating in stub mode - SDK not available');
      return;
    }

    this.sdk = new addon.ZoomSDK();
  }

  async initialize(): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Initialize called');
      return;
    }

    this.sdk.initialize({
      domain: 'https://zoom.us',
      enableLog: true,
      logSize: 10
    });
  }

  ensureSdkAvailable(): void {
    if (!this.isStubMode) {
      return;
    }

    const addonError =
      addonLoadError instanceof Error ? addonLoadError.message : String(addonLoadError || 'unknown error');

    throw new Error(
      [
        '[Zoom] Zoom SDK native addon is not available.',
        'Expected native addon: services/vexa-bot/build/Release/zoom_sdk_wrapper.node',
        'Expected SDK library: services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so',
        'Zoom Meeting SDK binaries are proprietary and must be downloaded separately from Zoom.',
        `Addon load error: ${addonError}`
      ].join(' ')
    );
  }

  async authenticate(clientId: string, clientSecret: string): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Authenticate called');
      return;
    }

    return new Promise((resolve, reject) => {
      this.sdk.onAuthResult((result: any) => {
        if (result.success) {
          console.log('[Zoom SDK] Authentication successful');
          resolve();
        } else {
          reject(new Error(`Auth failed: ${result.code}`));
        }
      });

      const jwt = this.generateJWT(clientId, clientSecret);
      this.sdk.authenticate({ jwt });
    });
  }

  async joinMeeting(meetingUrl: string): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Join meeting called:', meetingUrl);
      return;
    }

    const { meetingId, password } = this.parseMeetingUrl(meetingUrl);

    return new Promise((resolve, reject) => {
      this.sdk.onMeetingStatus((status: any) => {
        console.log('[Zoom SDK] Meeting status:', status.status);

        if (status.status === 'in_meeting') {
          resolve();
        }
        if (status.status === 'failed' || status.status === 'ended') {
          reject(new Error(`Meeting ${status.status}: code ${status.code}`));
        }
      });

      this.sdk.joinMeeting({
        meetingNumber: meetingId,
        displayName: this.config.botName,
        password: password || '',
        onBehalfToken: this.config.obfToken || ''
      });
    });
  }

  async joinAudio(): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Join audio called');
      return;
    }

    this.sdk.joinAudio();
  }

  async onActiveSpeakerChange(callback: (activeUserIds: number[]) => void): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Speaker change callback registered');
      return;
    }

    this.sdk.onActiveSpeakerChange(callback);
    console.log('[Zoom SDK] Speaker change callback registered');
  }

  getUserInfo(userId: number): { userId: number; userName: string; isHost: boolean } | null {
    if (this.isStubMode) {
      return { userId, userName: `Stub User ${userId}`, isHost: false };
    }

    try {
      const userInfo = this.sdk.getUserInfo(userId);
      return userInfo;
    } catch (error) {
      console.log(`[Zoom SDK] Failed to get user info for ${userId}: ${error}`);
      return null;
    }
  }

  async startRecording(onAudioData: (buffer: Buffer, sampleRate: number) => void): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Start recording called');
      return;
    }

    this.sdk.onAudioData(onAudioData);
    this.sdk.startRecording({ audioChannel: 'mixed', sampleRate: 16000 });
  }

  async stopRecording(): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Stop recording called');
      return;
    }

    this.sdk.stopRecording();
  }

  async leaveMeeting(): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Leave meeting called');
      return;
    }

    this.sdk.leaveMeeting();
  }

  async cleanup(): Promise<void> {
    if (this.isStubMode) {
      console.log('[Zoom SDK Stub] Cleanup called');
      return;
    }

    await this.stopRecording();
    this.sdk.cleanup();
  }

  // Expose sdk for removal monitor
  get nativeSDK(): any {
    return this.sdk;
  }

  // Utility methods
  private parseMeetingUrl(url: string): { meetingId: string; password?: string } {
    const urlObj = new URL(url);
    const meetingId = urlObj.pathname.match(/\/j\/(\d+)/)?.[1];
    const password = urlObj.searchParams.get('pwd') || undefined;

    if (!meetingId) {
      throw new Error(`Invalid Zoom meeting URL: ${url}`);
    }

    return { meetingId, password };
  }

  private generateJWT(clientId: string, clientSecret: string): string {
    // Simple HMAC-SHA256 JWT for Zoom SDK authentication
    const header = Buffer.from(JSON.stringify({
      alg: 'HS256',
      typ: 'JWT'
    })).toString('base64url');

    const now = Math.floor(Date.now() / 1000);
    const payload = Buffer.from(JSON.stringify({
      appKey: clientId,
      iat: now,
      exp: now + 86400, // 24 hours
      tokenExp: now + 86400
    })).toString('base64url');

    const signature = crypto
      .createHmac('sha256', clientSecret)
      .update(`${header}.${payload}`)
      .digest('base64url');

    return `${header}.${payload}.${signature}`;
  }
}
