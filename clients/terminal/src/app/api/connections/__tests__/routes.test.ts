import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { NextRequest } from 'next/server';
vi.mock('../broker', async (importOriginal) => ({ ...(await importOriginal<typeof import('../broker')>()), brokerCall: vi.fn() }));
import { BrokerFault, brokerCall } from '../broker';
import { POST, GET } from '../[...action]/route';
const origin='https://app.dev.vexa.ai';
let warnings: string[] = [];
beforeEach(()=>{vi.clearAllMocks();process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN=origin;warnings=[];vi.spyOn(console,'warn').mockImplementation((l:string)=>{warnings.push(l);});});
afterEach(()=>{vi.restoreAllMocks();delete process.env.NEXTAUTH_URL;});
function request(path:string,body:unknown,from=origin){return new NextRequest(origin+'/api/connections/'+path,{method:'POST',headers:{origin:from,'content-type':'application/json'},body:JSON.stringify(body)});}
test('refuses cross origin and caller-chosen identity',async()=>{
 expect((await POST(request('request',{provider:'google_email'},'https://evil.test'))).status).toBe(403);
 expect((await POST(request('request',{provider:'google_email',actor:'victim'}))).status).toBe(400);
 expect(brokerCall).not.toHaveBeenCalled();
});
test('without a declared origin the terminal falls back to NEXTAUTH_URL, and with neither it refuses',async()=>{
 delete process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN;
 vi.mocked(brokerCall).mockResolvedValue({connection_id:'a'.repeat(32),status:'awaiting_user'});
 expect((await POST(request('request',{provider:'google_email'}))).status).toBe(403);
 process.env.NEXTAUTH_URL=origin+'/';
 expect((await POST(request('request',{provider:'google_email'}))).status).toBe(200);
});
test('fixed provider requests return no OAuth URL or secret',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({connection_id:'a'.repeat(32),status:'awaiting_user',secret:'must-not-return'});
 const r=await POST(request('request',{provider:'google_calendar'}));
 expect(await r.json()).toEqual({connection_id:'a'.repeat(32),status:'awaiting_user'});
});
test('arbitrary proxy routes and destinations refused',async()=>{
 expect((await POST(request('../../operator/google',{}))).status).toBe(404);
 expect((await GET(new NextRequest(origin+'/api/connections/secret'))).status).toBe(404);
 expect(brokerCall).not.toHaveBeenCalled();
});
test('callback returns status-only popup completion without exposing provider credentials',async()=>{
 const {connectionCallback}=await import('../callback');
 vi.mocked(brokerCall).mockResolvedValue({status:'connected',access_token:'secret-token'});
 const response=await connectionCallback(new NextRequest(origin+'/api/auth/callback/google?state=vxc_fixture&code=secret-code'));
 const html=await response.text();
 expect(response.status).toBe(200);expect(response.headers.get('location')).toBeNull();
 expect(html).toContain('BroadcastChannel');expect(html).toContain('"connected"');
 expect(html).not.toContain('secret-code');expect(html).not.toContain('secret-token');
 expect(response.headers.get('Content-Security-Policy')).toContain("default-src 'none'");
});
test('a failed callback is refused and logged by kind, never swallowed silently',async()=>{
 const {connectionCallback}=await import('../callback');
 vi.mocked(brokerCall).mockRejectedValue(new Error('unexpected secret-code'));
 const html=await (await connectionCallback(new NextRequest(origin+'/api/auth/callback/google?state=vxc_fixture&code=secret-code'))).text();
 expect(html).toContain('"refused"');
 expect(warnings.map(w=>JSON.parse(w))).toContainEqual(expect.objectContaining({event:'broker_fault',source:'terminal',kind:'unexpected'}));
 expect(warnings.join('')).not.toContain('secret-code');
 delete process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN;
 expect((await connectionCallback(new NextRequest(origin+'/api/auth/callback/google?state=vxc_x'))).status).toBe(503);
 expect(warnings.map(w=>JSON.parse(w)).at(-1)).toMatchObject({kind:'config'});
});
test('custom secret submission projects no credential back to the UI and carries the host confirmation',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({connection_id:'a'.repeat(32),status:'ready',value:'hidden'});
 const response=await POST(request('a'.repeat(32)+'/custom-secret',{value:'fixture-secret',endpoint:'https://api.example.test/v1',confirmed_host:'api.example.test'}));
 expect(await response.json()).toEqual({connection_id:'a'.repeat(32),status:'ready'});
 expect(vi.mocked(brokerCall).mock.calls[0][2]).toMatchObject({confirmed_host:'api.example.test'});
 expect((await POST(request('a'.repeat(32)+'/custom-secret',{value:'x',actor:'other'}))).status).toBe(400);
 expect((await POST(request('a'.repeat(32)+'/custom-secret',{value:'x',confirmed_host:7}))).status).toBe(400);
});
test('an OAuth application save carries every typed host, and nothing else',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({connection_id:'a'.repeat(32),status:'awaiting_user'});
 const body={client_id:'id',client_secret:'s',setup_request:'r',confirmed_host:'tokens.example.test',confirmed_hosts:['api.example.test']};
 expect((await POST(request('a'.repeat(32)+'/oauth-application',body))).status).toBe(200);
 expect(vi.mocked(brokerCall).mock.calls.at(-1)![2]).toMatchObject({confirmed_hosts:['api.example.test']});
 for (const bad of [{confirmed_hosts:'api.example.test'},{confirmed_hosts:[7]},{confirmed_hosts:['a','b','c','d','e']}])
  expect((await POST(request('a'.repeat(32)+'/oauth-application',{...body,...bad}))).status).toBe(400);
});
test('an actionable broker refusal reaches the person; anything else is one generic line',async()=>{
 vi.mocked(brokerCall).mockRejectedValueOnce(new BrokerFault('http_409',409,'Confirm the destination host before saving'));
 const r=await POST(request('a'.repeat(32)+'/oauth-application',{client_id:'id',client_secret:'s',setup_request:'r'}));
 expect([r.status,(await r.json()).error]).toEqual([409,'Confirm the destination host before saving']);
 vi.mocked(brokerCall).mockRejectedValueOnce(new BrokerFault('transport'));
 const down=await POST(request('a'.repeat(32)+'/delete',{}));
 expect([down.status,(await down.json()).error]).toEqual([503,'Connection setup unavailable. Please try again.']);
 vi.mocked(brokerCall).mockRejectedValueOnce(new BrokerFault('unauthenticated'));
 expect((await GET(new NextRequest(origin+'/api/connections/list'))).status).toBe(401);
});
test('upcoming reads only a READY calendar, over a fixed 24-hour window, and returns title + start only',async()=>{
 const cal='c'.repeat(32), mail='e'.repeat(32), off='d'.repeat(32);
 vi.mocked(brokerCall).mockImplementation(async (method:string,path:string)=>{
  if(method==='GET')return {connections:[{id:mail,provider:'google_email',status:'ready'},{id:off,provider:'google_calendar',status:'disconnected'},{id:cal,provider:'google_calendar',status:'ready'}]};
  return {events:[
   {summary:'Acme — pricing review',start:{dateTime:'2026-10-11T15:00:00+02:00'},description:'secret agenda',attendees:[{email:'x@acme.test'}],htmlLink:'https://calendar.google.com/x',location:'Room 1',meeting_url:'https://us02web.zoom.us/j/81234567890?pwd=abc'},
   {summary:'Bad link',start:{dateTime:'2026-10-11T15:30:00+02:00'},meeting_url:'javascript:alert(1)'},
   {summary:'Gone',status:'cancelled',start:{dateTime:'2026-10-11T16:00:00+02:00'}},
   {start:{date:'2026-10-12'}},
  ]};
 });
 const before=Date.now();
 const r=await GET(new NextRequest(origin+'/api/connections/upcoming?time_max=2099-01-01&connection='+off));
 expect(r.status).toBe(200);
 // the call's link rides along (founder 2026-10-16: "join this call" in one step), https only
 expect(await r.json()).toEqual({events:[{title:'Acme — pricing review',start:'2026-10-11T15:00:00+02:00',meeting_url:'https://us02web.zoom.us/j/81234567890?pwd=abc'},{title:'Bad link',start:'2026-10-11T15:30:00+02:00'},{title:'',start:null}]});
 const reads=vi.mocked(brokerCall).mock.calls.filter(c=>c[0]==='POST');
 expect(reads.map(c=>c[1])).toEqual([`/api/connections/${cal}/read`]);
 const body=reads[0][2] as {action:string;time_min:string;time_max:string};
 expect(body.action).toBe('calendar.events');
 expect(Date.parse(body.time_max)-Date.parse(body.time_min)).toBe(24*60*60*1000);
 expect(Date.parse(body.time_min)).toBeGreaterThanOrEqual(before-1000);
});
test('upcoming with no ready calendar asks the broker for nothing but the list',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({connections:[{id:'e'.repeat(32),provider:'google_email',status:'ready'}]});
 expect(await (await GET(new NextRequest(origin+'/api/connections/upcoming'))).json()).toEqual({events:[]});
 expect(vi.mocked(brokerCall).mock.calls.every(c=>c[0]==='GET')).toBe(true);
});
test('upcoming fails loud as one generic line, never a calendar body',async()=>{
 vi.mocked(brokerCall).mockRejectedValueOnce(new BrokerFault('transport'));
 const r=await GET(new NextRequest(origin+'/api/connections/upcoming'));
 expect([r.status,(await r.json()).error]).toEqual([503,'Calendar unavailable. Try again.']);
});
test('authorize passes only the calendar-write grant through, and nothing else',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({authorize_url:'https://accounts.google.com/o/oauth2/v2/auth?x=1'});
 const id='a'.repeat(32);
 expect((await POST(request(id+'/authorize',{grant:'calendar_write'}))).status).toBe(200);
 expect(vi.mocked(brokerCall).mock.calls.at(-1)).toEqual(['POST',`/api/connections/${id}/authorize`,{grant:'calendar_write'}]);
 expect((await POST(request(id+'/authorize',{}))).status).toBe(200);
 expect(vi.mocked(brokerCall).mock.calls.at(-1)?.[2]).toEqual({});
 expect((await POST(request(id+'/authorize',{grant:'gmail_send'}))).status).toBe(400);
 expect((await POST(request(id+'/authorize',{grant:'calendar_write',scope:'x'}))).status).toBe(400);
 expect(brokerCall).toHaveBeenCalledTimes(2);
});
