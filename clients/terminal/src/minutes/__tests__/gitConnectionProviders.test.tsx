/** Connections → Git lists every configured provider, each with its own saved-token state and field;
 *  a token is saved against the provider it was typed for. */
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GitConnection } from '../GitConnection';
import { getGitToken, setGitToken } from '../../data/workspaceApi';
import { listGitProviders } from '../../data/gitProviders';
vi.mock('../../data/workspaceApi', () => ({ getGitToken: vi.fn(), setGitToken: vi.fn(), readDeployKey: vi.fn().mockResolvedValue({}) }));
vi.mock('../../data/gitProviders', async (original) => ({
  ...(await original<typeof import('../../data/gitProviders')>()),
  listGitProviders: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('lists each provider with label, host, saved state and scope hint; saves per provider', async () => {
  vi.mocked(getGitToken).mockResolvedValue({ set: true, masked: '••••1111', provider: 'github' });
  vi.mocked(listGitProviders).mockResolvedValue({
    default: 'github',
    providers: [
      { name: 'github', kind: 'github', label: 'GitHub', base_url: 'https://github.com', host: 'github.com', nested_groups: false, can_create: true, token_saved: true, masked: '••••1111' },
      { name: 'corp', kind: 'gitlab', label: 'Corp GitLab', base_url: 'https://git.example.com', host: 'git.example.com', nested_groups: true, can_create: true, token_saved: false, masked: null },
    ],
  });
  vi.mocked(setGitToken).mockResolvedValue({ set: true, masked: '••••2222', provider: 'corp' });
  render(<GitConnection />);
  const corp = await screen.findByRole('group', { name: 'Corp GitLab (git.example.com)' });
  expect(within(corp).getByText('No saved token')).toBeTruthy();
  expect(within(corp).getByText(/api \+ write_repository/)).toBeTruthy();
  expect(within(corp).getByText(/Maintainer if the group restricts project creation/)).toBeTruthy();
  const gh = screen.getByRole('group', { name: 'GitHub (github.com)' });
  expect(within(gh).getByText('Token saved securely (••••1111)')).toBeTruthy();
  expect(screen.getByText(/kept in the credential broker and sent only to that provider's host/)).toBeTruthy();
  fireEvent.change(within(corp).getByLabelText('Corp GitLab token'), { target: { value: 'fixture-only-secret' } });
  fireEvent.click(within(corp).getByRole('button', { name: 'Save securely' }));
  expect(await within(corp).findByText('Token saved securely (••••2222)')).toBeTruthy();
  expect(setGitToken).toHaveBeenCalledWith('fixture-only-secret', 'corp');
});
