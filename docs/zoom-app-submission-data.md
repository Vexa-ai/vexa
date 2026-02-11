# Zoom App Submission Data — Vexa Meeting Bot

**Do not submit to Zoom without explicit permission.** This document is the single source of what will be submitted.

---

## 1. Basic Information (Production)

| Field | Value |
|-------|--------|
| **Developer contact – Name** | Dmtiry Grankin |
| **Developer contact – Email** | dmitry@vexa.ai |
| **Developer contact – Role** | Developer |
| **Management model** | User-managed |
| **Production OAuth redirect URL** | `https://vexa.ai/auth/zoom/callback` |
| **Deauthorization endpoint** | `https://vexa.ai/webhooks/zoom/deauthorize` |

**Development OAuth redirect URLs (Zoom App settings):** In the Zoom developer portal, under **Develop → [Vexa Meeting Bot] → Basic Information** with **Development** selected, ensure these redirect URLs are present (add any that are missing; you can have multiple):

| Redirect URL | Use |
|--------------|-----|
| `http://localhost:8056/auth/zoom/callback` | Local bot-manager OAuth (e.g. backend/local test). |
| `http://localhost:3001/auth/zoom/callback` | Local dashboard OAuth (e.g. Vexa Dashboard on port 3001). |

No trailing slashes. A 4700 error means the redirect_uri your app sent is not in this whitelist.

**Production OAuth redirect URL:** This is the URL where Zoom sends the user after they authorize the app. For the hosted Vexa service it must be the production callback that your app actually serves (e.g. `https://vexa.ai/auth/zoom/callback`). For self-hosted deployments, each deployment would use its own base URL (e.g. `https://your-domain.com/auth/zoom/callback`). The value above is correct for **vexa.ai hosted**.

**Event Subscription (General Features):** Not required. Event Subscription is for subscribing to Zoom events (meeting started/ended, participant joined, etc.) and receiving webhooks. Vexa does not use it: the bot gets meeting state from the **Zoom Meeting SDK** (in-call status, removal) and from internal bot-manager callbacks. The only Zoom → Vexa webhook we use is **deauthorization**, which is configured in Basic Information, not under Event Subscription. Leave Event Subscription disabled in Production.

---

## 1b. Surface (Features → Surface)

Vexa is a **meeting-participant bot** that joins via the **Meeting SDK** (server-side). It does **not** run an in-Zoom-client app (no sidebar, no Team Chat bot, no in-meeting UI). Configure Surface as follows:

| Setting | Value / Action |
|--------|-----------------|
| **Home URL** | Leave **empty**. We don’t display a web view inside the Zoom client. |
| **Domain Allow List** | Not needed (only used when Home URL is set). |
| **Select where to use your app** | Enable **Meetings** only (and **Webinars** only if you want the bot to join webinars the same way). Leave **Team Chat, Rooms, Phone, Contact Center, Whiteboard, Virtual Agent, Events, Mail, Workflows** unchecked. |
| **In-client App Features** | None required. **Zoom App SDK**, **Guest Mode**, **In-Client OAuth**, **Collaborate Mode**, **Team Chat Subscription**, **App Shortcuts** are for apps that run inside the Zoom client UI. Vexa runs as a backend bot that joins as a participant via Meeting SDK — no in-client features. |
| **Mobile / Zoom Rooms / PWA** | Not required for a meeting-join bot. |

**Summary:** Minimal Surface config: **Meetings** (and optionally Webinars) as products; no Home URL, no in-client features, no Team Chat.

---

## 2. Scopes (Production)

| Scope | Purpose |
|-------|---------|
| `user:read:zak` | View a user's Zoom Access Key (required by Meeting SDK) |
| `user:read:token` | View a user's token — used to generate OBF tokens for joining meetings outside the app’s Zoom account |

*Scope descriptions (how data is used):*  
- **user:read:token:** Used only to obtain short-lived OBF (on-behalf) tokens so the bot can join Zoom meetings on behalf of an authorized user. OBF tokens are not stored; they are requested per meeting and used once. OAuth access/refresh tokens are stored server-side only to mint OBF tokens and are removed on user deauthorization (see Technical Design security Q3).

---

## 3. App Listing — App Information

| Field | Value |
|-------|--------|
| **App name** | Vexa Meeting Bot |
| **Company name** | Vexa AI |
| **Short description** | Real-time meeting transcription API |
| **Long description** | Vexa joins meetings and provides real-time transcription via a simple API. Users can add the bot to their meetings to get live transcripts. Open-source meeting transcription (Apache 2.0); supports Google Meet, Microsoft Teams, and Zoom. |
| **Marketplace category** | Transcription & Translation |
| **Industry vertical** | *(Select in Zoom UI — e.g. “Productivity” or “Business”)* |
| **Adding your app** | From Marketplace |
| **List on other marketplaces?** | Yes *(GitHub, self-hosted)* |

### Icons (from Vexa-Dashboard)

- **Light mode app icon:** Use `Vexa-Dashboard/public/icons/vexalight.svg` (export or resize to meet Zoom: 160×160 px min, JPG/PNG, &lt; 1 MB).
- **Dark mode app icon:** Use `Vexa-Dashboard/public/icons/vexadark.svg` (same specs).
- **Alternative:** `Vexa-Dashboard/public/icons/icons8-zoom-96.png` or logo from `vexa/assets/logodark.svg` resized to 160×160.

### Cover image

- **Spec:** 1824×176 px, JPG/JPEG/GIF/PNG, &lt; 2 MB.
- **Source:** User-provided screenshot (Join a Meeting modal / dashboard) saved in workspace, or use a cropped/resized image from `Vexa-Dashboard/docs/screenshots/` (e.g. dashboard or join flow). Ensure left side accounts for logo overlay per Zoom’s guidelines.

### App gallery

- Up to 6 images (or 5 images + 1 video). Suggested: screenshots from `Vexa-Dashboard/docs/screenshots/` (e.g. 01-dashboard.png, 02-join-meeting.png, 06-live-transcript.png) resized to 1200×780 px if required.

---

## 4. App Listing — Links & Support (from vexa.ai)

| Field | URL / Value |
|-------|-------------|
| **Privacy policy** | https://vexa.ai/privacy |
| **Terms of service** | https://vexa.ai/terms *(add if not yet published; otherwise use existing terms URL from vexa.ai footer)* |
| **Support URL / Email** | https://vexa.ai or info@vexa.ai |
| **Website / marketing** | https://vexa.ai |
| **Documentation / developer** | https://vexa.ai/get-started |

*Use the same links as on the vexa.ai website where applicable.*

---

## 5. App Listing — EU & Discoverability (from vexa.ai)

**Data handling / privacy (summary from https://vexa.ai/privacy):**

- **Controller:** Vexa.ai Inc. (account, billing, website, analytics). **Processor:** Vexa.ai Inc. (meeting/transcript data on user instructions).
- **Contact:** info@vexa.ai
- **What we process:** Account (name, email, company, billing, plan, usage); meetings as processor (metadata, transcript text, speaker labels, timestamps); diagnostics (logs/IDs, no audio/video stored); website/analytics (cookies, pages, device, referrers).
- **Purposes & legal bases:** Provide service (contract); security/fraud (legitimate interests); billing/compliance (legal obligation/contract); analytics/marketing cookies (consent where required).
- **Retention:** Transcripts until user deletes (user controls); account/billing for account lifetime + statutory; logs/analytics for short operational windows or as required by law.
- **International transfers:** Primary hosting Frankfurt, DE; may use other regions; SCCs and supplementary measures where outside EEA/UK.
- **Rights (EEA/UK):** Access, rectify, erase, restrict, object, portability, withdraw consent; contact info@vexa.ai; right to lodge complaint with supervisory authority.
- **Subprocessors:** https://vexa.ai/legal/subprocessors  
- **Security:** https://vexa.ai/legal/security  
- **Do not sell (US):** We do not sell personal information; state privacy rights honored as applicable.

**EU/EEA data processing:** As above; EU contacts and lead supervisory authority (e.g. CNPD Portugal if applicable) as stated in the full privacy notice at https://vexa.ai/privacy.

**Discoverability:** Show in Zoom App Marketplace; regions as per Zoom’s options (typically all where the app is legally available).

---

## 6. Technical Design (open-source, from README + zoom-app-setup)

**Summary for Zoom:**

- **Project:** Vexa is an **open-source** (Apache 2.0) meeting transcription platform. The Zoom integration is part of the public repository: https://github.com/Vexa-ai/vexa.
- **Architecture (Zoom):**  
  - Vexa uses the **Zoom Meeting SDK for Linux** to join meetings natively (no browser automation).  
  - The bot authenticates with a **JWT** from the app’s Client ID and Client Secret, joins the meeting, captures audio via the SDK, and streams it to the transcription pipeline (e.g. WhisperLive) for real-time transcription and speaker identification.  
  - Flow: `Zoom Meeting SDK (C++ N-API) → Raw PCM Audio → WhisperLive → Transcription`; active-speaker events used for speaker attribution.
- **Data flow:** User starts a bot via API (or Dashboard) with meeting URL/ID; bot joins via SDK JWT (or OBF when implemented); audio is streamed to transcription service; transcripts are delivered via API/WebSocket and optionally stored in the user’s database; no Zoom audio stored beyond processing.
- **Security:** Credentials (Client ID/Secret) stored server-side; HTTPS only; tokens (JWT/OBF) short-lived and used per meeting; self-hosted option for full data sovereignty (see README).
- **Reused from repo:** Architecture and flow are described in `vexa/README.md` (What is Vexa, How it works, self-hosting) and `vexa/docs/zoom-app-setup.md` (Zoom app type, SDK, OAuth, OBF).

**Implementation references (for review):** Zoom OAuth/OBF: `vexa/services/bot-manager/app/zoom_obf.py`; bot start and token persistence: `vexa/services/bot-manager/app/main.py` (lines 617–651); user model: `vexa/libs/shared-models/shared_models/models.py`; Zoom SDK/join: `vexa/services/vexa-bot/core/src/platforms/zoom/`, `vexa/docs/zoom-app-setup.md`.

*(Paste or adapt the “How it works” and “Zoom” sections from README and zoom-app-setup.md into Zoom’s Technical Design text fields if character limits allow.)*

---

## 7. Technical Design — Security questions (codebase validation)

These answers should match the Zoom Technical Design → Security form. Validated against the **vexa** codebase. All paths below are relative to the repo root (e.g. `vexa/`).

### 1. Does your app use transport layer security (TLS) and only support TLS 1.2 or above for all network traffic, including Zoom user's data?

**Recommended answer: Yes**

**Evidence:**

- All Zoom API calls use **HTTPS** only:
  - **`vexa/services/bot-manager/app/zoom_obf.py`** (lines 104, 155): `POST https://zoom.us/oauth/token`, `GET https://api.zoom.us/v2/users/me/token` — no `http://` anywhere.
- Outbound HTTP client is **httpx.AsyncClient** with default settings (no `verify=False`); see `zoom_obf.py` lines 100–104 and 151–155 (client created without custom SSL config).
- User-facing endpoints are served behind HTTPS in production (vexa.ai); TLS is enforced at the reverse proxy. No app code disables TLS or forces TLS &lt; 1.2.

**File references:** `vexa/services/bot-manager/app/zoom_obf.py` (Zoom OAuth/OBF HTTP calls).

---

### 2. Is the integration utilizing verification tokens or secret tokens and x-zm-signature header to confirm the incoming Webhook Events are coming from Zoom?

**Recommended answer: No** *(until the deauthorization webhook is implemented with verification)*

**Evidence:**

- Zoom’s deauthorization endpoint is configured as `https://vexa.ai/webhooks/zoom/deauthorize`, but **no handler for this URL exists in the repo**:
  - **`vexa/services/api-gateway/main.py`** — no `/webhooks/zoom` or `/auth/zoom` routes.
  - **`vexa/services/bot-manager/app/main.py`** — only `/bots` and `/bots/internal/callback/*` routes (see `@app.post` / `@app.get` definitions).
  - **`vexa/services/admin-api/app/main.py`** — no Zoom webhook routes.
- No use of `x-zm-signature` or Zoom webhook secret token anywhere (search: `zm-signature`, `zm_signature`, `deauthorize`).
- Zoom expects verification via the **Webhook Secret Token** and `x-zm-signature` (HMAC-SHA256 of `v0:{timestamp}:{body}`). After implementing the deauthorization endpoint and verifying the signature before processing, switch this answer to **Yes**.

**File references:** `vexa/services/api-gateway/main.py`, `vexa/services/bot-manager/app/main.py`, `vexa/services/admin-api/app/main.py` (where Zoom webhook routes would live).

---

### 3. Does your application collect, store, log, or retain Zoom user data, including Zoom OAuth Tokens?

**Recommended answer: Yes**

**Evidence:**

- The app **does store Zoom OAuth tokens** (access_token, refresh_token, expires_at) in the user record:
  - **Model:** **`vexa/libs/shared-models/shared_models/models.py`** (line 20) — `User.data` is a JSONB column; Zoom OAuth is stored under `user.data["zoom"]["oauth"]`.
  - **Read:** **`vexa/services/bot-manager/app/zoom_obf.py`** — `_get_nested_zoom_oauth()` (lines 14–23), `resolve_zoom_access_token_from_user_data()` (lines 45–63), `get_zoom_refresh_token()` (lines 66–71) read from `user_data["zoom"]["oauth"]`.
  - **Write:** **`vexa/services/bot-manager/app/main.py`** (lines 636–651) — on token refresh, bot-manager updates `current_user.data["zoom"]["oauth"]` with `access_token`, `refresh_token`, `expires_at` (and optionally `scope`) and commits via `await db.commit()`; called from the `/bots` start flow (around line 617 onward).
- **Short-lived OBF tokens** are not stored: minted in `zoom_obf.py` `mint_zoom_obf_token()` (lines 142–179) and passed once to the bot; not written to DB.
- Zoom’s question explicitly includes “Zoom OAuth Tokens”; we retain those, so the accurate answer is **Yes**. In the Zoom form “at rest” field, use the text below in the Zoom form "at rest" field.

**Text for Zoom form — "Provide details on how this data is protected at rest":**

> Zoom OAuth tokens (access_token, refresh_token, expires_at) are stored in the user record in PostgreSQL in a JSONB column (`User.data`, see `vexa/libs/shared-models/shared_models/models.py`). The database is encrypted at rest per deployment (e.g. cloud provider; production uses standard infrastructure encryption). Tokens are written/updated only when refreshing the user's Zoom OAuth session (`vexa/services/bot-manager/app/main.py`, lines 636–651) and are read solely to mint short-lived OBF tokens per meeting (`vexa/services/bot-manager/app/zoom_obf.py`). They are not logged. Stored tokens are removed when the user deauthorizes the app (deauthorization webhook to be implemented at `https://vexa.ai/webhooks/zoom/deauthorize`).

**File references:**
- `vexa/libs/shared-models/shared_models/models.py` — `User.data` schema.
- `vexa/services/bot-manager/app/zoom_obf.py` — read Zoom OAuth from user data; mint OBF token.
- `vexa/services/bot-manager/app/main.py` — persist refreshed Zoom OAuth into `current_user.data` (lines 636–651).

---

## 7b. Technical Design — Application Development (codebase validation)

These answers should match the Zoom Technical Design → Overview → Application Development section. Validated against the **vexa** codebase. If you answer **Yes** to any, Zoom may require evidence (documents/reports).

### 1. Do you have a secure software development process (SSDLC)?

**Recommended answer: No**

**Explanation:** SSDLC typically means a documented process that includes security in design (e.g. threat modeling), secure coding practices, security-focused code review, and security testing as part of the lifecycle.

**Evidence:**

- No formal SSDLC policy or process document in the repo (no “secure development”, “SSDLC”, or “security development lifecycle” references).
- **`vexa/SECURITY.md`** is a generic template: supported versions table and “Reporting a Vulnerability” placeholder only; it does not describe a secure development process.
- **`vexa/docs/zoom-app-setup.md`** (around line 247) mentions “OWASP Top 10 security testing” as a submission prerequisite for Zoom—that is a recommendation, not evidence of an implemented SSDLC.
- No security design review, threat model, or secure-coding policy files found.

**File references:** `vexa/SECURITY.md`, `vexa/docs/zoom-app-setup.md`. No SSDLC docs under `.github/`, `docs/`, or repo root.

---

### 2. Does your application undergo SAST (Static Application Security Test) and/or DAST (Dynamic Application Security Test)?

**Recommended answer: No**

**Explanation:** SAST = static analysis of source/code (e.g. Bandit, Semgrep, CodeQL, SonarQube security rules). DAST = dynamic testing against a running app (e.g. OWASP ZAP, Burp, automated vulnerability scans).

**Evidence:**

- No SAST/DAST tools or steps in the **vexa** repo: grep for `SAST`, `DAST`, `snyk`, `semgrep`, `sonar`, `codeql`, `bandit`, `safety` (Python security) returns no CI or config usage in the main app.
- The only **`.github/workflows`** under vexa is **`vexa/services/WhisperLive/.github/workflows/ci.yml`** (WhisperLive submodule): it runs unit tests and **flake8** (style/syntax), not security scanning.
- No Bandit, Safety, or other Python security linters in bot-manager, api-gateway, or admin-api CI. No evidence of DAST (e.g. ZAP) in workflows or docs.

**File references:** No workflow files in `vexa/.github/workflows/`; `vexa/services/WhisperLive/.github/workflows/ci.yml` has tests + flake8 only.

---

### 3. Does the application periodically undergo 3rd Party Application penetration testing?

**Recommended answer: No**

**Explanation:** Zoom is asking whether an external party runs penetration tests on your application and you have reports to provide if asked.

**Evidence:**

- No references to **penetration testing**, **pen test**, **security audit**, or **3rd party** security assessment in the codebase or docs.
- No links to or mentions of external audit reports, pentest summaries, or scope-of-work for security testing.

**File references:** None. If you later commission a pentest, you can answer **Yes** and provide the report (or summary) when Zoom requests evidence.

---

## 8. Credentials (reference only — not submitted as form data)

- **Production Client ID:** `2QxcHnEtQgO_i8O3Wsh10g`
- **Production Client Secret:** *(stored in Zoom only)*
- **Development Client ID:** `pdDHNGrCQt6Ge2wgFD46sg`
- **Development Client Secret:** *(in local .env)*

---

## Checklist before submitting

- [ ] Developer contact email set to **dmitry@vexa.ai**
- [ ] **Development** OAuth redirect URL (Basic Information, Development): **http://localhost:8056/auth/zoom/callback** (exactly, no trailing slash).
- [ ] Production OAuth redirect URL: **https://vexa.ai/auth/zoom/callback**
- [ ] Deauthorization URL: **https://vexa.ai/webhooks/zoom/deauthorize**
- [ ] Short description: **Real-time meeting transcription API**
- [ ] Long description: **Vexa joins meetings and provides real-time transcription via simple API. Users can add the bot to their meetings to get live transcripts.**
- [ ] Icons from **Vexa-Dashboard** (vexalight.svg / vexadark.svg or assets as above)
- [ ] Cover image: user screenshot or **Vexa-Dashboard** screenshots (1824×176 px)
- [ ] Links: **Privacy** vexa.ai/privacy, **Support** info@vexa.ai, **Website** vexa.ai, **Docs** vexa.ai/get-started
- [ ] EU/data: content from **vexa.ai** privacy notice
- [ ] Technical design: **open-source**, reuse **README** + **zoom-app-setup.md**
- [ ] Technical Design → Security: **TLS 1.2+** Yes; **Webhook verification (x-zm-signature)** No (until deauthorize endpoint is implemented); **Store Zoom OAuth tokens** Yes (see §7).
- [ ] Technical Design → Overview → **Application Development**: **SSDLC** No; **SAST/DAST** No; **3rd party pen testing** No (see §7b). If you change any to Yes, provide evidence when Zoom requests it.
- [ ] Technical Design → Overview → **Architecture Diagram**: upload `vexa/docs/zoom-architecture-diagram.png` (see `vexa/docs/zoom-architecture-diagram.md` for source and flow).
- [ ] **Submit only after explicit permission**
