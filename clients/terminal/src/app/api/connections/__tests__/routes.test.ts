import { beforeEach, expect, test, vi } from 'vitest';
import { NextRequest } from 'next/server';
vi.mock('../broker',()=>({brokerCall:vi.fn()}));
import { brokerCall } from '../broker';
import { POST, GET } from '../[...action]/route';
const origin='https://app.dev.vexa.ai';
beforeEach(()=>{vi.clearAllMocks();process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN=origin;});
function request(path:string,body:unknown,from=origin){return new NextRequest(origin+'/api/connections/'+path,{method:'POST',headers:{origin:from,'content-type':'application/json'},body:JSON.stringify(body)});}
test('refuses cross origin and caller-chosen identity',async()=>{
 expect((await POST(request('request',{provider:'google_email'},'https://evil.test'))).status).toBe(403);
 expect((await POST(request('request',{provider:'google_email',actor:'victim'}))).status).toBe(400);
 expect(brokerCall).not.toHaveBeenCalled();
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
test('custom secret submission projects no credential back to the UI',async()=>{
 vi.mocked(brokerCall).mockResolvedValue({connection_id:'a'.repeat(32),status:'ready',value:'hidden'});
 const response=await POST(request('a'.repeat(32)+'/custom-secret',{value:'fixture-secret',endpoint:'https://api.example.test/v1'}));
 expect(await response.json()).toEqual({connection_id:'a'.repeat(32),status:'ready'});
 expect((await POST(request('a'.repeat(32)+'/custom-secret',{value:'x',actor:'other'}))).status).toBe(400);
});
