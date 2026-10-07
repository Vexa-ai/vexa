import { randomBytes } from 'node:crypto';
/** Connection consent uses the already-registered login callback, with its own state namespace. */
import { NextRequest, NextResponse } from 'next/server';
import { brokerCall } from './broker';
export async function connectionCallback(req: NextRequest) {
  const origin=process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN;
  if (!origin) return new NextResponse('Connections are not configured',{status:503});
  const q=new URLSearchParams();
  for (const k of ['state','code','error']) q.set(k,req.nextUrl.searchParams.get(k)||'');
  let result='refused';
  try {
    const response=await brokerCall('GET','/api/auth/callback/google?'+q.toString());
    result=response.status==='connected'?'connected':'refused';
  } catch { /* Never echo provider codes, tokens or errors. */ }
  const nonce=randomBytes(18).toString('base64');
  const html=`<!doctype html><html><head><meta charset="utf-8"><title>Account connection</title></head><body><h1>${result==='connected'?'Account connected':'Authorization not completed'}</h1><p>You can close this window and return to Minutes.</p><script nonce="${nonce}">const channel=new BroadcastChannel('vexa:connection-consent');channel.postMessage({status:${JSON.stringify(result)}});channel.close();window.close();</script></body></html>`;
  return new NextResponse(html,{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':`default-src 'none'; script-src 'nonce-${nonce}'; base-uri 'none'; frame-ancestors 'none'`}});
}
