import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CrmPanel } from '../CrmPanel';
import { ASK_CHAT_EVENT } from '../../canvas/actions';
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it('renders an authorized card and follows related records without replacing the chat URL', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
    const body = JSON.parse(options.body);
    const result = body.operation === 'describe' ? {objects: [{object_type: 'Account'}]} : {
      id: body.record_id, object_type: 'Account', revision: 1,
      fields: {Name: body.record_id === 'a' ? 'First account' : 'Related account'},
      narrative: '', proposals: [], sources: [],
      links: body.record_id === 'a' ? [{field: 'Parent',record_id: 'b',label:'Related account'}] : [],
    };
    return {ok:true,json:async()=>result};
  }));
  const close = vi.fn(); const before = window.location.href;
  render(<CrmPanel recordId="a" onClose={close} onCollapse={()=>{}} />);
  await screen.findByText('First account', {selector:'div'});
  expect(screen.queryByRole('button',{name:'Browse records'})).toBeNull();
  expect(screen.queryByLabelText('New value')).toBeNull();
  const ask=vi.fn();window.addEventListener(ASK_CHAT_EVENT,ask,{once:true});
  fireEvent.click(screen.getByRole('button',{name:'Configure card'}));
  expect((ask.mock.calls[0][0] as CustomEvent).detail.prompt).toContain('object type Account');
  fireEvent.click(screen.getByRole('link',{name:'Related account'}));
  await screen.findByText('Related account', {selector:'div'});
  expect(window.location.href).toBe(before);
  fireEvent.click(screen.getByRole('button',{name:'Back to pages'}));
  expect(close).toHaveBeenCalledOnce();
});
it('shows a service refusal instead of an invented record',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,json:async()=>({detail:'Record not found'})})));
  render(<CrmPanel recordId="restricted" onClose={()=>{}} onCollapse={()=>{}} />);
  await waitFor(()=>expect(screen.getByRole('alert').textContent).toBe('Record not found'));
});
it('keeps the filtered table page when navigating to a record and back',async()=>{
 const fetch=vi.fn(async (_url,options)=>{
  const body=JSON.parse(options.body);
  const record={id:'a',object_type:'Account',revision:1,fields:{Name:'Table account',Status:'Open'},narrative:null};
  return {ok:true,json:async()=>body.operation==='describe'?{objects:[{object_type:'Account'}]}:body.operation==='search'?{records:[record],next_offset:null}:record};
 });vi.stubGlobal('fetch',fetch);
 render(<CrmPanel recordId={'/crm?object=Account&filters=%7B%22Status%22%3A%22Open%22%7D'} onClose={()=>{}} onCollapse={()=>{}}/>);
 await screen.findByRole('table');
 expect(fetch.mock.calls.map(c=>JSON.parse(c[1].body)).find(b=>b.operation==='search').filters).toEqual({Status:'Open'});
 fireEvent.click(screen.getByRole('link',{name:'Table account'}));
 await screen.findByRole('button',{name:'← Back to table'});
 fireEvent.click(screen.getByRole('button',{name:'← Back to table'}));
 await screen.findByRole('table');
 expect(fetch.mock.calls.filter(c=>JSON.parse(c[1].body).operation==='search')).toHaveLength(1);
});
