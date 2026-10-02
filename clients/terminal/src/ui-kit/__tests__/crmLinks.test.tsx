import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
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
