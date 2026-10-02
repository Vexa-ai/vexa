import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CrmPanel } from '../CrmPanel';
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
  await screen.findByRole('heading',{name:'First account'});
  expect(screen.queryByRole('button',{name:'Browse records'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Parent Related account'}));
  await screen.findByRole('heading',{name:'Related account'});
  expect(window.location.href).toBe(before);
  fireEvent.click(screen.getByRole('button',{name:'Back to pages'}));
  expect(close).toHaveBeenCalledOnce();
});
it('shows a service refusal instead of an invented record',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,json:async()=>({detail:'Record not found'})})));
  render(<CrmPanel recordId="restricted" onClose={()=>{}} onCollapse={()=>{}} />);
  await waitFor(()=>expect(screen.getByRole('alert').textContent).toBe('Record not found'));
});
