import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ConnectionsPanel, CONNECTIONS_OPEN } from '../ConnectionsPanel';
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
test('repeated setup request reopens a dismissed panel without a duplicate connection',async()=>{
 let signal='first'; let poll:()=>void=()=>{};
 vi.spyOn(document,'hidden','get').mockReturnValue(false);
 vi.spyOn(window,'setInterval').mockImplementation((fn:any,ms:any)=>{if(ms===5000)poll=fn;return 42 as unknown as ReturnType<typeof window.setInterval>;});
 vi.stubGlobal('fetch',vi.fn().mockImplementation(async()=>({ok:true,json:async()=>({connections:[{id:'same',provider:'google_email',label:'Gmail',status:'awaiting_user',created:1,setup_request:signal}]})})));
 render(<ConnectionsPanel/>);
 await act(async()=>{});
 expect(screen.queryByRole('dialog')).toBeNull();
 fireEvent(window,new Event(CONNECTIONS_OPEN));
 await screen.findByRole('region',{name:'Connections'});
 fireEvent.click(screen.getByRole('button',{name:'Close connections'}));
 await act(async()=>{poll();});
 expect(screen.queryByRole('dialog')).toBeNull();
 signal='second';await act(async()=>{poll();});
 expect(screen.getByRole('dialog')).toBeTruthy();
 vi.restoreAllMocks();
});
test('an MCP-created pending request opens secure panel without starting consent',async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[{id:'a'.repeat(32),provider:'google_email',label:'Gmail',status:'awaiting_user',created:1}]})});
 vi.stubGlobal('fetch',fetch);render(<ConnectionsPanel/>);
 fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'google_email',label:'Gmail'}}));
 expect(await screen.findByRole('dialog',{name:'Connections'})).toBeTruthy();
 expect(await screen.findByRole('button',{name:'Continue with Google'})).toBeTruthy();
 expect(fetch.mock.calls.every(([path])=>(path.endsWith('/list')||path.endsWith('/git-token')))).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'Close connections'}));
 expect(screen.queryByRole('dialog')).toBeNull();
});
test('account menu opens page; explicit provider request does not auto-consent',async()=>{
 const fetch=vi.fn().mockImplementation(async(path:string)=>({ok:true,json:async()=>path.endsWith('/request')?{connection_id:'b'.repeat(32)}:{connections:[]}}));
 vi.stubGlobal('fetch',fetch);render(<ConnectionsPanel/>);
 fireEvent(window,new Event(CONNECTIONS_OPEN));
 fireEvent.click(await screen.findByRole('button',{name:'Connect Google Calendar'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([p])=>p.endsWith('/request'))).toBe(true));
 expect(fetch.mock.calls.some(([p])=>p.endsWith('/authorize'))).toBe(false);
});
test('consent opens a popup without navigating Minutes',async()=>{
 const replace=vi.fn(),close=vi.fn();const popup={document:{title:'',body:{textContent:''}},location:{replace},close,opener:window};
 vi.spyOn(window,'open').mockReturnValue(popup as any);
 vi.stubGlobal('fetch',vi.fn().mockImplementation(async(path:string)=>({ok:true,json:async()=>path.endsWith('/authorize')?{authorize_url:'https://accounts.google.com/o/oauth2/v2/auth?state=fixture'}:{connections:[{id:'pending',provider:'google_email',label:'Gmail',status:'awaiting_user',created:1}]}})));
 render(<ConnectionsPanel/>);fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'google_email'}}));fireEvent.click(await screen.findByRole('button',{name:'Continue with Google'}));
 await waitFor(()=>expect(replace).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/v2/auth?state=fixture'));
 expect(popup.opener).toBeNull();expect(close).not.toHaveBeenCalled();
});
test('blocked popup explains recovery before authorization is requested',async()=>{
 vi.spyOn(window,'open').mockReturnValue(null);
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[{id:'pending',provider:'google_email',label:'Gmail',status:'awaiting_user',created:1}]})});vi.stubGlobal('fetch',fetch);
 render(<ConnectionsPanel/>);fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'google_email'}}));fireEvent.click(await screen.findByRole('button',{name:'Continue with Google'}));
 expect((await screen.findByRole('alert')).textContent).toContain('Allow popups');
 expect(fetch.mock.calls.some(([p])=>p.endsWith('/authorize'))).toBe(false);
});

test('a focused custom request hides unrelated accounts and renders prepared fields',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[
 {id:'oura',provider:'custom_secret',label:'Oura',status:'awaiting_user',setup:{endpoint:'https://api.example.com/resource',method:'GET',secret_label:'Access token',fields:[]}},
 {id:'telegram',provider:'custom_secret',label:'Telegram',status:'ready'},
 {id:'gmail',provider:'google_email',label:'Work mail',status:'ready'}]})}));
 render(<ConnectionsPanel/>);
 fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'custom_secret',label:'Oura'}}));
 expect(await screen.findByLabelText('Access token')).toBeTruthy();
 expect(screen.queryByText('Telegram')).toBeNull();expect(screen.queryByText('Work mail')).toBeNull();
 expect(screen.queryByLabelText('HTTPS endpoint')).toBeNull();
 expect(screen.getByText(/Prepared connection/).textContent).toContain('https://api.example.com/resource');
});

test('opening and closing reports ownership of the shell panel without an overlay',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[]})}));
 const visible=vi.fn();render(<ConnectionsPanel onOpenChange={visible}/>);
 fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'github'}}));
 const panel=await screen.findByRole('dialog');
 expect(visible).toHaveBeenLastCalledWith(true);
 expect(panel.style.position).not.toBe('fixed');
 expect(panel.style.gridColumn).toBe('3');
 expect(panel.getAttribute('aria-modal')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Close connections'}));
 expect(visible).toHaveBeenLastCalledWith(false);
});

test('inventory occupies the main workspace and restores on close',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[]})}));
 const mode=vi.fn();render(<ConnectionsPanel onModeChange={mode}/>);
 fireEvent(window,new Event(CONNECTIONS_OPEN));
 const page=await screen.findByRole('region',{name:'Connections'});
 expect(page.style.gridColumn).toBe('2 / 4');
 expect(page.getAttribute('data-connections-surface')).toBe('page');
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(mode).toHaveBeenLastCalledWith('page');
 fireEvent.click(screen.getByRole('button',{name:'Close connections'}));
 expect(mode).toHaveBeenLastCalledWith(null);
});

test('delete is explicit and also available for an unfinished request',async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[{id:'a'.repeat(32),provider:'custom_secret',label:'Fixture',status:'awaiting_user'}]})});
 vi.stubGlobal('fetch',fetch);render(<ConnectionsPanel/>);
 fireEvent(window,new Event(CONNECTIONS_OPEN));
 fireEvent.click(await screen.findByRole('button',{name:'Delete Fixture connection'}));
 expect(fetch.mock.calls.some(([p])=>p.endsWith('/delete'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Delete connection',exact:true}));
 await waitFor(()=>expect(fetch.mock.calls.some(([p])=>p.endsWith('/delete'))).toBe(true));
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Delete Fixture connection'})).toBeNull());
});

test('connected secrets show status and Edit instead of empty setup fields',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[{id:'saved',provider:'custom_secret',label:'Service',status:'ready',setup:{endpoint:'https://api.example.com',method:'GET',secret_label:'API key',fields:[]}}]})}));
 render(<ConnectionsPanel/>);fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'custom_secret',label:'Service'}}));
 await screen.findByRole('button',{name:'Edit connection'});
 expect(screen.queryByLabelText('API key')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Edit connection'}));
 expect(screen.getByLabelText('API key')).toBeTruthy();
});

test('a new service request replaces an already focused service',async()=>{
 let signal='old';let poll:()=>void=()=>{};
 vi.spyOn(document,'hidden','get').mockReturnValue(false);
 vi.spyOn(window,'setInterval').mockImplementation((fn:any,ms:any)=>{if(ms===5000)poll=fn;return 42 as any;});
 vi.stubGlobal('fetch',vi.fn().mockImplementation(async()=>({ok:true,json:async()=>({connections:[
 {id:'gmail',provider:'google_email',label:'Work',status:'ready',setup_request:'unchanged'},
 {id:'telegram',provider:'custom_secret',label:'Telegram',status:'awaiting_user',setup_request:signal}
 ]})})));
 render(<ConnectionsPanel/>);await act(async()=>{});
 fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'google_email'}}));
 await screen.findByText('Work');
 signal='new-request';await act(async()=>{poll();});
 expect(screen.getAllByText('Telegram').length).toBeGreaterThan(0);
 expect(screen.queryByText('Work')).toBeNull();
});

test('an arbitrary OAuth definition renders application fields without service-specific UI',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({connections:[{id:'generic',provider:'custom_secret',label:'Example service',status:'awaiting_user',setup_request:'proposal',setup:{endpoint:'https://api.example.com/data',method:'GET',secret_label:'Unused API token',fields:[],oauth:{authorization_url:'https://login.example.com/authorize',token_url:'https://api.example.com/token',scopes:['read']}}}]})}));
 render(<ConnectionsPanel/>);fireEvent(window,new CustomEvent(CONNECTIONS_OPEN,{detail:{provider:'custom_secret',label:'Example service'}}));
 expect(await screen.findByLabelText('Client ID')).toBeTruthy();
 expect(screen.getByLabelText('Client secret').getAttribute('type')).toBe('password');
 expect(screen.queryByLabelText('Unused API token')).toBeNull();
 expect(screen.getByText(/Authorization: https/).textContent).toContain('login.example.com');
});
