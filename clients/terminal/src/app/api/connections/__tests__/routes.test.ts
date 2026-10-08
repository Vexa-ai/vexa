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
