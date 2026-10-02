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
