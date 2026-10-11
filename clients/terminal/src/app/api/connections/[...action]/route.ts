/** Closed route table: no arbitrary proxy destination or caller-supplied identity. */
import { NextRequest, NextResponse } from 'next/server';
import { BrokerFault, brokerCall, publicOrigin } from '../broker';
const headers = {'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'};
const cid = /^[a-f0-9]{32}$/;
const host = (v: unknown) => v === undefined || (typeof v === 'string' && v.length <= 253);
const hosts = (v: unknown) => v === undefined || (Array.isArray(v) && v.length <= 4 && v.every(h => typeof h === 'string' && h.length <= 253));

/** A broker failure, already logged by kind in brokerCall, as the browser sees it: the broker's own
 *  fixed sentence when the person can act on it, otherwise one generic line. */
function failure(e: unknown, fallback: string) {
  if (e instanceof BrokerFault && e.detail && e.status) return NextResponse.json({error: e.detail}, {status: e.status, headers});
  if (e instanceof BrokerFault && e.kind === 'unauthenticated') return NextResponse.json({error: 'Sign in to manage connections'}, {status: 401, headers});
  if (!(e instanceof BrokerFault)) console.warn(JSON.stringify({event: 'broker_fault', source: 'terminal', kind: 'unexpected', role: 'human'}));
  return NextResponse.json({error: fallback}, {status: 503, headers});
}

/** The empty chat's "Prepare for <title> at <time>" chip needs the next meeting on the person's
 *  connected calendar. This is the broker's own calendar read (`calendar.events`, audited there),
 *  as the signed-in person, over a FIXED window — the next 24 hours, `UPCOMING_WINDOW_MS` in
 *  `minutes/proposals.ts` — with no caller input at all. The answer is projected down to the title
 *  and the start of each event: no description, attendees, location or link reaches the browser,
 *  and nothing is stored. No ready calendar → an empty list, and the broker is not asked. */
const UPCOMING_MS = 24 * 60 * 60 * 1000;
const CAL_MAX = 3;
type CalEvent = { summary?: unknown; status?: unknown; start?: { dateTime?: unknown } | null };
async function upcoming() {
  const rows = ((await brokerCall('GET','/api/connections'))?.connections ?? []) as {id:string;provider:string;status:string}[];
  const calendars = rows.filter(r => r.provider==='google_calendar' && r.status==='ready' && cid.test(r.id)).slice(0, CAL_MAX);
  const now = Date.now();
  const window = {action:'calendar.events', time_min:new Date(now).toISOString(), time_max:new Date(now+UPCOMING_MS).toISOString(), limit:10};
  const events: {title:string;start:string|null}[] = [];
  for (const c of calendars) {
    const read = await brokerCall('POST',`/api/connections/${c.id}/read`,window);
    for (const e of ((read?.events ?? []) as CalEvent[])) {
      if (e?.status === 'cancelled') continue;
      events.push({title: typeof e?.summary==='string' ? e.summary.slice(0,200) : '', start: typeof e?.start?.dateTime==='string' ? e.start.dateTime : null});
    }
  }
  return {events};
}

export async function GET(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (path.endsWith('/connections/list')) {
    try { return NextResponse.json(await brokerCall('GET','/api/connections'),{headers}); }
    catch (e) { return failure(e, 'Connections unavailable. Check your sign-in or try again.'); }
  }
  if (path.endsWith('/connections/upcoming')) {
    try { return NextResponse.json(await upcoming(),{headers}); }
    catch (e) { return failure(e, 'Calendar unavailable. Try again.'); }
  }
  return new NextResponse(null,{status:404});
}
export async function POST(req: NextRequest) {
  // The origin comes from deployment configuration, never forwarded Host headers.
  const origin = publicOrigin();
  if (!origin || req.headers.get('origin') !== origin) return new NextResponse(null,{status:403,headers});
  const parts=req.nextUrl.pathname.split('/').filter(Boolean).slice(2);
  try {
    if (parts.length===1 && parts[0]==='request') {
      const body=await req.json();
      if (!['google_email','google_calendar','custom_secret'].includes(body.provider) || Object.keys(body).some(k=>!['provider','label'].includes(k)) || (body.label!==undefined && (typeof body.label!=='string'||body.label.length>80))) return new NextResponse(null,{status:400,headers});
      const result=await brokerCall('POST','/api/setup',{provider:body.provider,label:body.label?.trim()||({google_email:'Gmail',google_calendar:'Google Calendar',custom_secret:'Custom secret'} as Record<string,string>)[body.provider]});
      return NextResponse.json({connection_id:result.connection_id,status:result.status},{headers});
    }
    if (parts.length===2 && cid.test(parts[0]) && parts[1]==='oauth-application') {
      const body=await req.json();
      if(typeof body.client_id!=='string'||typeof body.client_secret!=='string'||typeof body.setup_request!=='string'||!host(body.confirmed_host)||!hosts(body.confirmed_hosts)||Object.keys(body).some(k=>!['client_id','client_secret','setup_request','confirmed_host','confirmed_hosts'].includes(k))||body.client_id.length>200||body.client_secret.length>2000)return new NextResponse(null,{status:400,headers});
      return NextResponse.json(await brokerCall('POST',`/api/connections/${parts[0]}/oauth-application`,body),{headers});
    }
    if (parts.length===2 && cid.test(parts[0]) && parts[1]==='custom-secret') {
      const body=await req.json();
      if (typeof body.value!=='string'||body.value.length>65536||!host(body.confirmed_host)||Object.keys(body).some(k=>!['value','endpoint','header','scheme','method','fields','setup_request','confirmed_host'].includes(k)))return new NextResponse(null,{status:400,headers});
      const result=await brokerCall('POST',`/api/connections/${parts[0]}/custom-secret`,body);
      return NextResponse.json({connection_id:result.connection_id,status:result.status},{headers});
    }
    if (parts.length===2 && cid.test(parts[0]) && ['authorize','disconnect','delete'].includes(parts[1])) {
      return NextResponse.json(await brokerCall('POST',`/api/connections/${parts[0]}/${parts[1]}`,{}),{headers});
    }
    return new NextResponse(null,{status:404,headers});
  } catch (e) { return failure(e, 'Connection setup unavailable. Please try again.'); }
}
