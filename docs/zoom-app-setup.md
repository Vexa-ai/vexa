# Zoom App Setup Guide

How to register and configure a Zoom app for Vexa's meeting bot. This guide covers both **self-hosted** (open source) deployments and the **hosted Vexa service**.

## Overview

Vexa uses the [Zoom Meeting SDK for Linux](https://developers.zoom.us/docs/meeting-sdk/linux/) to join Zoom meetings natively (no browser automation). The bot authenticates with a JWT generated from your app's Client ID and Client Secret, joins meetings, captures audio via the SDK, and streams it to WhisperLive for real-time transcription with speaker identification.

### Architecture

```
Zoom Meeting SDK (C++ N-API) → Raw PCM Audio → WhisperLive → Transcription
                              → Active Speaker Events → Speaker Attribution
```

### What You Need

| Component | Purpose |
|-----------|---------|
| Zoom Marketplace App | Provides Client ID + Client Secret for SDK auth |
| OAuth Flow (for external meetings) | Required for OBF tokens after March 2, 2026 |
| App Review (for external meetings) | Zoom must approve your app to join meetings outside your account |

## App Types

Zoom offers several app types. For Vexa, you need a **General App** with the **Meeting SDK** feature enabled:

| App Type | Use Case | External Meetings |
|----------|----------|-------------------|
| Meeting SDK App | SDK-only, internal meetings | No (same account only) |
| **General App + Meeting SDK** | Full OAuth + SDK | **Yes** (after review + OBF tokens) |
| Server-to-Server OAuth | API-only, no SDK | N/A |

> **Important**: If you only need to join meetings within your own Zoom account (e.g., internal deployment), a basic Meeting SDK app is sufficient. For joining meetings hosted by anyone (the typical use case), you need a General App.

## Step 1: Create the Zoom App

1. Go to [Zoom App Marketplace](https://marketplace.zoom.us/) and sign in
2. Click **Develop** → **Build App**
3. Select **General App** as the app type
4. Click **Create**

### App Information

Fill in the required fields:

| Field | Description | Example |
|-------|-------------|---------|
| App Name | Display name (must be unique, cannot contain "Zoom") | `Vexa Meeting Bot` |
| Short Description | One-line summary | `Real-time meeting transcription with speaker identification` |
| Long Description | Detailed description of what the app does | See below |
| Company Name | Your organization | `Vexa AI` |
| Developer Contact | Email for Zoom to reach you | `dev@vexa.ai` |

### Management Model

Choose how users authorize your app:

- **User-managed** (recommended for most deployments): Individual users grant access to their own meetings
- **Admin-managed**: Organization admins grant access for all users in their Zoom organization

## Step 2: Enable Meeting SDK

1. Navigate to the **Embed** tab in the sidebar
2. Toggle **Meeting SDK** to ON
3. This enables SDK credentials (Client ID + Client Secret) separate from OAuth credentials

## Step 3: Configure OAuth (Required for External Meetings)

### Redirect URL

Set the OAuth redirect URL where Zoom sends the authorization code after user consent:

```
https://your-domain.com/auth/zoom/callback
```

For development:
```
http://localhost:8056/auth/zoom/callback
```

### OAuth Scopes

Add the following scopes under the **Scopes** tab:

| Scope | Purpose | Required |
|-------|---------|----------|
| `user:read:token` | Generate OBF tokens for joining external meetings | **Yes** (for external meetings) |

> **Least privilege**: Only request scopes you actually need. The `user:read:token` scope is the minimum required for the OBF flow. Additional scopes slow down the review process.

### Deauthorization

Configure the deauthorization notification URL:

```
https://your-domain.com/webhooks/zoom/deauthorize
```

Zoom requires that when a user removes your app, you stop accessing their data and delete stored tokens.

## Step 4: Get Credentials

### Development Credentials

On the **App Credentials** section of the **Basic Information** tab:

- **Client ID**: Used as `ZOOM_CLIENT_ID` in Vexa configuration
- **Client Secret**: Used as `ZOOM_CLIENT_SECRET` in Vexa configuration

> **Development credentials only work for meetings hosted within your Zoom account.** To join external meetings, you must complete app review (Step 6).

### Production Credentials

After app review approval, production credentials become available. Replace development credentials with production ones for your deployment.

## Step 5: Configure Vexa

### Environment Variables

Add to your `.env` or `docker-compose.yml`:

```bash
ZOOM_CLIENT_ID=your_client_id_here
ZOOM_CLIENT_SECRET=your_client_secret_here
```

These are passed to the bot container via the bot-manager service. See `docker-compose.yml` lines 72-73.

### Verify

Test the bot joins a meeting within your Zoom account:

```bash
ZOOM_MEETING_URL="https://us05web.zoom.us/j/YOUR_MEETING_ID?pwd=YOUR_PASSWORD" \
ZOOM_CLIENT_ID="your_client_id" \
ZOOM_CLIENT_SECRET="your_client_secret" \
./services/vexa-bot/run-zoom-bot.sh
```

## Step 6: Implement OBF Token Flow (External Meetings)

> **Deadline: March 2, 2026** — After this date, Zoom enforces OBF tokens for all Meeting SDK apps joining meetings outside their own account.
>
> **Implementation status:** OBF tokens are **not yet implemented** in the vexa-bot codebase. The native SDK header (`meeting_service_interface.h`) supports `onBehalfToken` in the join params, but `BotConfig` lacks an `obfToken` field and `sdk-manager.ts:joinMeeting()` does not pass it to the SDK. This must be wired up before the deadline for external meeting support.

### What is an OBF Token?

An On Behalf Of (OBF) token proves that a real meeting participant has authorized your app to join their meeting. The authorizing user must be present in the meeting for the bot to join and remain connected.

### OAuth Authorization Flow

1. **User authorizes your app** via Zoom OAuth:
   ```
   GET https://zoom.us/oauth/authorize
     ?response_type=code
     &client_id={CLIENT_ID}
     &redirect_uri={REDIRECT_URI}
   ```

2. **Exchange authorization code for tokens**:
   ```
   POST https://zoom.us/oauth/token
     ?grant_type=authorization_code
     &code={AUTH_CODE}
     &redirect_uri={REDIRECT_URI}
   Authorization: Basic base64({CLIENT_ID}:{CLIENT_SECRET})
   ```

3. **Store access + refresh tokens** securely for the user

### Generating OBF Tokens

For each meeting the bot needs to join:

```
GET https://api.zoom.us/v2/users/me/token
  ?type=onbehalf
  &meeting_id={MEETING_NUMBER}
Authorization: Bearer {USER_ACCESS_TOKEN}
```

This returns a short-lived, single-use OBF token tied to that specific meeting.

### Passing OBF Token to the SDK

The OBF token is passed via the `onBehalfToken` parameter when joining a meeting (SDK 6.6.10+).

**Required code changes to implement OBF support:**

1. Add `obfToken?: string` to `BotConfig` in `core/src/types.ts`
2. Pass `obfToken` through `BOT_CONFIG` JSON from the bot-manager
3. Update `ZoomSDKManager.joinMeeting()` in `core/src/platforms/zoom/sdk-manager.ts` to include `onBehalfToken` in the native SDK join params
4. Update `zoom_wrapper.cpp` to read and forward the `onBehalfToken` field to `meetingService->Join()`

### OBF Constraints

| Constraint | Detail |
|-----------|--------|
| Meeting-specific | Each token works for one meeting only |
| User presence required | Authorizing user must be in the meeting |
| Auto-disconnect | Bot disconnects when authorizing user leaves |
| Single-use | Cannot reuse tokens across sessions |
| 2-hour expiry | Generate tokens just before joining |
| Min SDK version | 5.17.5 (recommended: 6.6.10+) |

### Error Handling

SDK 6.6.10+ returns error code `MEETING_FAIL_AUTHORIZED_USER_NOT_INMEETING` when the authorizing user hasn't joined yet. Implement retry logic with 1-5 second delays.

## Step 7: Submit for App Review

### Prerequisites

Before submitting:

- [ ] App information is complete (name, description, icons, URLs)
- [ ] OAuth redirect URLs are configured for production
- [ ] `user:read:token` scope is added
- [ ] Deauthorization endpoint is implemented
- [ ] OBF token flow is working in development
- [ ] Zoom's native recording/streaming indicators are triggered
- [ ] Legal UI notices are implemented per SDK features used
- [ ] Production credentials are used (not development)

### Submission Checklist

Zoom reviews apps in three stages:

#### Stage 1: Completeness & Branding
- Accurate metadata and technical documentation
- App name is distinct (doesn't impersonate Zoom)
- Meaningful description of functionality
- Proper icons and screenshots

#### Stage 2: Functionality, Usability & Compliance
- Installation/uninstallation works as described
- OAuth flow works correctly
- Bot joins meetings and transcribes as expected
- Data handling follows Zoom's privacy requirements
- No ads shown to Zoom users

#### Stage 3: Security Review
- Technical design review of architecture
- OAuth scope evaluation (least privilege)
- OWASP Top 10 security testing
- Vulnerable dependency checks

### Test Plan

Provide a detailed test plan including:

1. Step-by-step instructions for testing the bot
2. Test Zoom account credentials (create a dedicated test account)
3. How to trigger a bot join via your API
4. Expected behavior: bot joins, transcribes, leaves
5. How to verify transcription output

### Submit

1. Navigate to **Submit** in the Zoom Marketplace developer portal
2. Complete the submission checklist
3. Upload required documentation
4. Submit for review

### Timeline

Review time varies based on app complexity. Apps with minimal scopes and clear documentation review faster. Expect 1-4 weeks.

## Alternative: Zoom RTMS (Real-Time Media Streams)

For deployments that don't want to manage a meeting bot, Zoom offers [Real-Time Media Streams](https://developers.zoom.us/docs/rtms/) — a server-side API that streams meeting audio/video directly via WebSocket without a bot joining the meeting.

| Aspect | Meeting SDK (Current) | RTMS |
|--------|----------------------|------|
| Bot in meeting | Yes (visible participant) | No (server-side) |
| User presence needed | Yes (OBF requirement) | No |
| Audio access | Native SDK raw audio | WebSocket stream |
| Cost | Free (SDK license) | Paid (Zoom Developer Pack) |
| Setup complexity | Higher (native C++ SDK) | Lower (WebSocket API) |
| Platform | Linux x86_64 only | Any (WebSocket client) |

## Self-Hosted vs Hosted Vexa

### Self-Hosted (Open Source)

Each self-hosted deployment registers its own Zoom app:

1. Create a General App on Zoom Marketplace (this guide)
2. Configure credentials in your `.env`
3. For internal-only meetings: done
4. For external meetings: implement OBF flow + submit for review

### Hosted Service (vexa.ai)

The hosted Vexa service at [vexa.ai](https://vexa.ai) uses a pre-approved Zoom app. Users authorize via OAuth and the service handles OBF token generation automatically.

## Troubleshooting

### Bot fails to join meeting
- Verify `ZOOM_CLIENT_ID` and `ZOOM_CLIENT_SECRET` are set
- Check the meeting URL format: `https://us05web.zoom.us/j/MEETING_NUMBER?pwd=PASSWORD`
- For external meetings, ensure OBF token is provided and the authorizing user is in the meeting

### Authentication errors
- JWT tokens expire after 24 hours; the SDK regenerates them automatically
- If using OBF tokens, ensure the user's OAuth access token is still valid (refresh if needed)

### "Invalid status transition" errors
- The bot state machine expects: `requested → joining → active → stopped`
- Ensure `callJoiningCallback()` is called before SDK authentication (see `strategies/join.ts`)

### Audio capture issues
- Primary: SDK raw audio capture (requires raw data license from Zoom)
- Fallback: PulseAudio capture from `zoom_sink.monitor`
- Check that the Xvfb virtual display and PulseAudio daemon are running (handled by `entrypoint.sh`)

## References

- [Zoom Meeting SDK Documentation](https://developers.zoom.us/docs/meeting-sdk/)
- [Meeting SDK Auth (JWT)](https://developers.zoom.us/docs/meeting-sdk/auth/)
- [OBF Token Transition Blog Post](https://developers.zoom.us/blog/transition-to-obf-token-meetingsdk-apps/)
- [Meeting SDK Feature Review Requirements](https://developers.zoom.us/docs/distribute/sdk-feature-review-requirements/)
- [App Review Process](https://developers.zoom.us/docs/distribute/app-review-process/)
- [Zoom RTMS](https://developers.zoom.us/docs/rtms/)
- [Zoom Developer Forum](https://devforum.zoom.us/)
