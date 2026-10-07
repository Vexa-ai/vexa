/** Closed route table: no arbitrary proxy destination or caller-supplied identity. */
import { NextRequest, NextResponse } from 'next/server';
import { brokerCall } from '../broker';
const headers = {'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'};
const cid = /^[a-f0-9]{32}$/;
export async function GET(req: NextRequest) {
  if (!req.nextUrl.pathname.endsWith('/connections/list')) return new NextResponse(null,{status:404});
  try { return NextResponse.json(await brokerCall('GET','/api/connections'),{headers}); }
  catch { return NextResponse.json({error:'Connections unavailable. Check your sign-in or try again.'},{status:503,headers}); }
}
export async function POST(req: NextRequest) {
  // The origin comes from deployment configuration, never forwarded Host headers.
  const origin = process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN;
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
      if(typeof body.client_id!=='string'||typeof body.client_secret!=='string'||typeof body.setup_request!=='string'||Object.keys(body).some(k=>!['client_id','client_secret','setup_request'].includes(k))||body.client_id.length>200||body.client_secret.length>2000)return new NextResponse(null,{status:400,headers});
      return NextResponse.json(await brokerCall('POST',`/api/connections/${parts[0]}/oauth-application`,body),{headers});
    }
    if (parts.length===2 && cid.test(parts[0]) && parts[1]==='custom-secret') {
      const body=await req.json();
      if (typeof body.value!=='string'||body.value.length>65536||Object.keys(body).some(k=>!['value','endpoint','header','scheme','method','fields','setup_request'].includes(k)))return new NextResponse(null,{status:400,headers});
      const result=await brokerCall('POST',`/api/connections/${parts[0]}/custom-secret`,body);
      return NextResponse.json({connection_id:result.connection_id,status:result.status},{headers});
    }
    if (parts.length===2 && cid.test(parts[0]) && ['authorize','disconnect','delete'].includes(parts[1])) {
      return NextResponse.json(await brokerCall('POST',`/api/connections/${parts[0]}/${parts[1]}`,{}),{headers});
    }
    return new NextResponse(null,{status:404,headers});
  } catch { return NextResponse.json({error:'Connection setup unavailable. Please try again.'},{status:503,headers}); }
}
