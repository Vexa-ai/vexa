import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OPEN_CRM_RECORD } from '../crmNavigation';
import { InternalLink, DocNavContext, isCrmHref } from '../docLinks';

afterEach(cleanup);
it('keeps a CRM record link navigable with its tenant and native record identity', () => {
  const open = vi.fn();
  const href = '/crm?tenant=demo&record=abc';
  render(<DocNavContext.Provider value={open}><InternalLink href={href}>Opportunity</InternalLink></DocNavContext.Provider>);
  const link = screen.getByRole('link', {name: 'Opportunity'});
  expect(link.tagName).toBe('A');
  expect(link.getAttribute('href')).toBe(href);
  expect(open).not.toHaveBeenCalled();
});
it('does not treat workspace documents or network-path URLs as CRM routes', () => {
  for (const href of ['/crm.md', '/crm-notes', '//crm?tenant=x', 'crm.md', '/workspaces/crm']) expect(isCrmHref(href)).toBe(false);
});

it('opens the CRM panel without navigating the chat or workspace', () => {
  const opened = vi.fn((event: Event) => event.preventDefault());
  window.addEventListener(OPEN_CRM_RECORD, opened);
  const before = window.location.href;
  render(<InternalLink href="/crm?record=record-123">Deal</InternalLink>);
  fireEvent.click(screen.getByRole('link', {name: 'Deal'}));
  expect(opened).toHaveBeenCalledOnce();
  expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({recordId: 'record-123', href: '/crm?record=record-123'});
  expect(window.location.href).toBe(before);
  window.removeEventListener(OPEN_CRM_RECORD, opened);
});

it('resolves an entity wikilink to a CRM card before a workspace document',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,status:200,json:async()=>({records:[{href:'/crm?record=crm-account'}]})})));
  const {Wikilink}=await import('../docLinks');
  render(<Wikilink title="Aldmere Strategic Investment Corporation"/>);
  const link=await screen.findByRole('link',{name:'Aldmere Strategic Investment Corporation'});
  expect(link.getAttribute('href')).toBe('/crm?record=crm-account');
  vi.unstubAllGlobals();
});
it('offers CRM name matches rather than guessing among duplicates',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,status:200,json:async()=>({records:[{href:'/crm?record=a'},{href:'/crm?record=b'}]})})));
  const {Wikilink}=await import('../docLinks');
  render(<Wikilink title="Shared name"/>);
  expect((await screen.findByRole('link',{name:'Shared name'})).getAttribute('href')).toBe('/crm?name=Shared%20name');
  vi.unstubAllGlobals();
});
it('keeps workspace resolution when the CRM module is disabled',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:404,json:async()=>({detail:'CRM is not enabled'})})));
  const {Wikilink}=await import('../docLinks');
  render(<Wikilink title="Workspace-only strategy"/>);
  const link=await screen.findByRole('link',{name:'Workspace-only strategy'});
  expect(link.getAttribute('title')).toContain('No doc for');
  expect(link.getAttribute('href')).toBeNull();
  vi.unstubAllGlobals();
});
it('does not fall back to workspace pages on CRM failures',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({detail:'CRM is temporarily unavailable'})})));
  const {Wikilink}=await import('../docLinks');
  render(<Wikilink title="Unavailable account"/>);
  expect((await screen.findByRole('link',{name:'Unavailable account'})).getAttribute('href')).toBe('/crm?name=Unavailable%20account');
  vi.unstubAllGlobals();
});
