# Vexa Dashboard (UI)

Vexa Dashboard is the open-source web UI for Vexa: join meetings, view live transcripts, manage users/tokens, and review transcript history.

Repo:

- https://github.com/Vexa-ai/Vexa-Dashboard

## What You Need

- A reachable Vexa API Gateway URL (typically `http://<host>:8056`)
- The Vexa **Admin API token**
  - For self-hosted deployments, this is the `ADMIN_API_TOKEN` you configure for Vexa.
  - In the dashboard, set it as `VEXA_ADMIN_API_KEY`.

## Run With Docker

```bash
docker run --rm -p 3000:3000 \
  -e VEXA_API_URL=http://your-vexa-host:8056 \
  -e VEXA_ADMIN_API_KEY=your_admin_api_token \
  vexaai/vexa-dashboard:latest
```

Then open `http://localhost:3000`.

## Local Development

```bash
git clone https://github.com/Vexa-ai/Vexa-Dashboard.git
cd Vexa-Dashboard
npm install
cp .env.example .env.local
# edit .env.local: VEXA_API_URL + VEXA_ADMIN_API_KEY
npm run dev
```

Local dev server runs on `http://localhost:3001`.

## Recording Playback

On completed meetings, the meeting detail page can show an audio playback strip (if a recording exists) and highlight transcript segments during playback.

Backend/storage details:

- [`docs/recording-storage.md`](recording-storage.md)

## Zoom Caveat

Zoom meeting joins require additional backend setup and (typically) Marketplace approval:

- [`docs/zoom-app-setup.md`](zoom-app-setup.md)

