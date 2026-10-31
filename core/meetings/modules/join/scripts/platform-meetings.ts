/**
 * platform-meetings — create and delete a real meeting per platform with the test accounts (#1970 s1).
 *   tsx scripts/platform-meetings.ts create <google_meet|zoom|teams> [--dry-run]
 *   tsx scripts/platform-meetings.ts delete <receipt.json> [--dry-run]
 * Credentials come from the env file named by VERIFY_SECRETS (default ~/vexa-test-rig/secrets.env).
 * Output is a receipt JSON with no token and no passcode. Only `fetch`; no dependency.
 */
import { readFileSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';

export type Platform = 'google_meet' | 'zoom' | 'teams';
export interface Receipt {
  platform: Platform; platform_meeting_id: string; native_meeting_id: string; meeting_url: string;
  space?: string; created_at: string;
  deleted?: { at: string; delete_status: number; verify_status: number; note: string };
}
/** In-process join details for the driver (s2). Never printed: the passcode stays out of receipts. */
export interface Created { receipt: Receipt; join: { meeting_url: string; passcode?: string } }

const PLATFORMS: Platform[] = ['google_meet', 'zoom', 'teams'];
const CAL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events';
const MEET = 'https://meet.googleapis.com/v2';
const ZOOM = 'https://api.zoom.us/v2';
const GRAPH = 'https://graph.microsoft.com/v1.0/me/onlineMeetings';

/** Drop query and fragment (Zoom `pwd=`, Teams context) so no passcode reaches a receipt. */
export const stripUrl = (u: string) => u.replace(/[?#].*$/, '');

function secrets(): Record<string, string> {
  const file = process.env.VERIFY_SECRETS || join(homedir(), 'vexa-test-rig', 'secrets.env');
  const out: Record<string, string> = {};
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m) out[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return out;
}
function need(s: Record<string, string>, k: string): string {
  if (!s[k]) throw new Error(`missing ${k} in VERIFY_SECRETS file`);
  return s[k];
}

async function tokenFrom(url: string, body: Record<string, string>, basic?: string): Promise<string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/x-www-form-urlencoded' };
  if (basic) headers.Authorization = `Basic ${basic}`;
  const r = await fetch(url, { method: 'POST', headers, body: new URLSearchParams(body) });
  const j: any = await r.json().catch(() => ({}));
  if (!r.ok || !j.access_token) throw new Error(`token grant ${new URL(url).host} -> ${r.status} ${j.error || ''}`);
  return j.access_token;
}

async function token(p: Platform): Promise<string> {
  const s = secrets();
  if (p === 'google_meet') return tokenFrom('https://oauth2.googleapis.com/token', {
    grant_type: 'refresh_token', client_id: need(s, 'GOOGLE_CLIENT_ID'),
    client_secret: need(s, 'GOOGLE_CLIENT_SECRET'), refresh_token: need(s, 'GOOGLE_REFRESH_TOKEN') });
  if (p === 'zoom') return tokenFrom('https://zoom.us/oauth/token',
    { grant_type: 'account_credentials', account_id: need(s, 'ZOOM_ACCOUNT_ID') },
    Buffer.from(`${need(s, 'ZOOM_CLIENT_ID')}:${need(s, 'ZOOM_CLIENT_SECRET')}`).toString('base64'));
  const body: Record<string, string> = { grant_type: 'refresh_token', client_id: need(s, 'MS_CLIENT_ID'),
    refresh_token: need(s, 'MS_REFRESH_TOKEN'), scope: 'OnlineMeetings.ReadWrite offline_access' };
  if (s.MS_CLIENT_SECRET) body.client_secret = s.MS_CLIENT_SECRET;
  return tokenFrom(`https://login.microsoftonline.com/${s.MS_TENANT_ID || 'common'}/oauth2/v2.0/token`, body);
}

async function call(tok: string, method: string, url: string, body?: unknown): Promise<{ status: number; json: any }> {
  const r = await fetch(url, { method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' } });
  const text = await r.text();
  let json: any = {}; try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text.slice(0, 200) }; }
  return { status: r.status, json };
}
function ok(res: { status: number; json: any }, what: string) {
  if (res.status < 200 || res.status > 299) throw new Error(`${what} -> ${res.status} ${JSON.stringify(res.json).slice(0, 300)}`);
  return res.json;
}

/** The requests each command makes, with no secrets: what --dry-run prints. */
export function plan(cmd: 'create' | 'delete', p: Platform): string[] {
  const t = { google_meet: ['POST https://oauth2.googleapis.com/token (refresh_token)'],
    zoom: ['POST https://zoom.us/oauth/token (account_credentials)'],
    teams: ['POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token (refresh_token)'] }[p];
  const steps = cmd === 'create' ? {
    google_meet: [`POST ${CAL}?conferenceDataVersion=1 (conferenceData.createRequest hangoutsMeet)`,
      `GET ${MEET}/spaces/<meeting_code>`, `PATCH ${MEET}/<space>?updateMask=config.accessType (OPEN)`],
    zoom: [`POST ${ZOOM}/users/me/meetings (waiting_room=false, join_before_host=true)`],
    teams: [`POST ${GRAPH} (lobbyBypassSettings.scope=everyone)`] } : {
    google_meet: [`POST ${MEET}/<space>:endActiveConference`, `DELETE ${CAL}/<event_id>`, `GET ${CAL}/<event_id>`],
    zoom: [`DELETE ${ZOOM}/meetings/<id>`, `GET ${ZOOM}/meetings/<id>`],
    teams: [`DELETE ${GRAPH}/<id>`, `GET ${GRAPH}/<id>`] };
  return [...t, ...steps[p]];
}

export async function createMeeting(p: Platform): Promise<Created> {
  const tok = await token(p);
  const created_at = new Date().toISOString();
  if (p === 'google_meet') {
    const start = new Date(Date.now() + 60_000), end = new Date(Date.now() + 3_600_000);
    const ev = ok(await call(tok, 'POST', `${CAL}?conferenceDataVersion=1`, {
      summary: 'vexa verify-speak-chat', start: { dateTime: start.toISOString() }, end: { dateTime: end.toISOString() },
      conferenceData: { createRequest: { requestId: `vexa-${Date.now()}`, conferenceSolutionKey: { type: 'hangoutsMeet' } } } }),
      'calendar events.insert');
    const code: string = ev.conferenceData?.conferenceId;
    if (!code) throw new Error('calendar event has no Meet conference');
    const receipt: Receipt = { platform: p, platform_meeting_id: ev.id, native_meeting_id: code,
      meeting_url: stripUrl(ev.hangoutLink || `https://meet.google.com/${code}`), created_at };
    try {
      const sp = ok(await call(tok, 'GET', `${MEET}/spaces/${code}`), 'meet spaces.get');
      receipt.space = sp.name;
      ok(await call(tok, 'PATCH', `${MEET}/${sp.name}?updateMask=config.accessType`, { config: { accessType: 'OPEN' } }),
        'meet spaces.patch OPEN');
    } catch (e) { await deleteMeeting(receipt).catch(() => undefined); throw e; }
    return { receipt, join: { meeting_url: receipt.meeting_url } };
  }
  if (p === 'zoom') {
    const m = ok(await call(tok, 'POST', `${ZOOM}/users/me/meetings`, { topic: 'vexa verify-speak-chat', type: 2,
      start_time: new Date().toISOString(), duration: 60,
      settings: { waiting_room: false, join_before_host: true, jbh_time: 0 } }), 'zoom meetings.create');
    return { receipt: { platform: p, platform_meeting_id: String(m.id), native_meeting_id: String(m.id),
      meeting_url: stripUrl(m.join_url), created_at }, join: { meeting_url: m.join_url, passcode: m.password } };
  }
  const start = new Date(), end = new Date(Date.now() + 3_600_000);
  const m = ok(await call(tok, 'POST', GRAPH, { subject: 'vexa verify-speak-chat',
    startDateTime: start.toISOString(), endDateTime: end.toISOString(),
    lobbyBypassSettings: { scope: 'everyone', isDialInBypassEnabled: true } }), 'graph onlineMeetings.create');
  return { receipt: { platform: p, platform_meeting_id: m.id,
    native_meeting_id: String(m.joinMeetingIdSettings?.joinMeetingId || '').replace(/\s/g, ''),
    meeting_url: stripUrl(m.joinWebUrl), created_at },
    join: { meeting_url: m.joinWebUrl, passcode: m.joinMeetingIdSettings?.passcode } };
}

/** Delete and verify; `platform_meeting_deleted` is true only when this returns deletedOk(receipt). */
export async function deleteMeeting(r: Receipt): Promise<Receipt> {
  const tok = await token(r.platform);
  let note = '', del: number, ver: number;
  if (r.platform === 'google_meet') {
    const end = r.space ? (await call(tok, 'POST', `${MEET}/${r.space}:endActiveConference`, {})).status : 0;
    del = (await call(tok, 'DELETE', `${CAL}/${r.platform_meeting_id}`)).status;
    const g = await call(tok, 'GET', `${CAL}/${r.platform_meeting_id}`);
    ver = g.status === 200 && g.json.status === 'cancelled' ? 410 : g.status;
    note = `space persists; conference ended (${end}), event ${ver === 410 ? 'cancelled' : `status ${g.status}`}`;
  } else {
    const url = r.platform === 'zoom' ? `${ZOOM}/meetings/${r.platform_meeting_id}` : `${GRAPH}/${r.platform_meeting_id}`;
    del = (await call(tok, 'DELETE', url)).status;
    ver = (await call(tok, 'GET', url)).status;
  }
  return { ...r, deleted: { at: new Date().toISOString(), delete_status: del, verify_status: ver, note } };
}

export const deletedOk = (r: Receipt) => !!r.deleted && r.deleted.delete_status >= 200
  && r.deleted.delete_status < 300 && [404, 410].includes(r.deleted.verify_status);

async function main(argv: string[]) {
  const dry = argv.includes('--dry-run');
  const [cmd, arg] = argv.filter((a) => a !== '--dry-run');
  if (cmd === 'create' && PLATFORMS.includes(arg as Platform)) {
    if (dry) return console.log(JSON.stringify({ dry_run: true, plan: plan('create', arg as Platform), receipt: {
      platform: arg, platform_meeting_id: '<id>', native_meeting_id: '<native>', meeting_url: '<url, passcode stripped>',
      created_at: '<iso>' } }, null, 2));
    return console.log(JSON.stringify((await createMeeting(arg as Platform)).receipt));
  }
  if (cmd === 'delete' && arg) {
    const receipt: Receipt = JSON.parse(readFileSync(arg, 'utf8'));
    if (!PLATFORMS.includes(receipt.platform)) {
      console.error(`receipt ${arg}: platform must be one of ${PLATFORMS.join('|')}, got ${JSON.stringify(receipt.platform)}`);
      process.exitCode = 2;
      return;
    }
    if (dry) {
      const p = receipt.platform;
      const ids = (s: string) => s.replace(/<(?:id|event_id)>/g, receipt.platform_meeting_id)
        .replace('<space>', receipt.space || '<space>');
      return console.log(JSON.stringify({ dry_run: true, plan: plan('delete', p).map(ids), deleted: { at: '<iso>',
        delete_status: '<2xx>', verify_status: '<404|410>', note: p === 'google_meet'
          ? 'space persists; conference ended (<status>), event cancelled' : '' } }, null, 2));
    }
    const r = await deleteMeeting(receipt);
    console.log(JSON.stringify({ ...r, platform_meeting_deleted: deletedOk(r) }));
    if (!deletedOk(r)) process.exitCode = 1;
    return;
  }
  console.error('usage: platform-meetings.ts create <google_meet|zoom|teams> | delete <receipt.json> [--dry-run]');
  process.exitCode = 2;
}

if (require.main === module) main(process.argv.slice(2)).catch((e) => { console.error(String(e?.message || e)); process.exit(1); });
