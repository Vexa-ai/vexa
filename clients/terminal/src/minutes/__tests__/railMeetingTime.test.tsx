/** A MEETING ROW SHOWS ITS TIME, NOT "Held" (founder, app.dev 2026-10-11, on a screenshot of the rail
 *  where meeting rows read "Acme  Held  ⋯" and, on hover, "Hel⋯": *"i think this 'held' does not
 *  make sense anymore"*).
 *
 *  Three things are pinned: a finished meeting's row carries no status word and shows its date in
 *  the time column like every chat row; a live meeting keeps its success "live" word and dot; and the
 *  ⋯ takes the time's place on hover/focus instead of drawing over the row — the time column is at
 *  least as wide as the row action and steps aside (visibility, so nothing shifts), as on a Calls row. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Rail } from '../Rail';
import { railRows, type Chat } from '../chats';
import type { MeetingMock } from '../../surfaces/meetingModel';
vi.mock('../AccountBadge', () => ({ AccountBadge: () => null }));
vi.mock('../../surfaces/chatActivity', () => ({ useChatActive: () => false }));

const NOW = Date.UTC(2026, 9, 11, 12, 0, 0);
const at = (mins: number) => new Date(NOW + mins * 60000).toISOString();
const meeting = (id: string, title: string, live_status: string, startMins: number): MeetingMock =>
  ({ id, title, status: live_status === 'active' ? 'live' : 'past', live_status,
     native_id: `n-${id}`, start_time: at(startMins) } as unknown as MeetingMock);
const chat = (over: Partial<Chat> & { id: string }): Chat => ({
  label: 'Notes', workspaces: ['personal', '_global'], artifacts: [], touched: true,
  createdAt: NOW - 60000, lastActivityAt: NOW - 1000, ...over,
});
const base: any = { hidden: 0, all: true, onAll: () => {}, selKey: null, onSelect: () => {}, onNewChat: () => {}, onDeleteChat: () => {} };

const HELD = meeting('7', 'Acme', 'completed', -2 * 24 * 60);       // two days ago: a weekday
const LIVE = meeting('8', 'Bot into Acme Zoom call', 'active', -5);

describe('a meeting row in the chat list', () => {
  afterEach(() => cleanup());

  it('a finished meeting shows no "Held" and no time column — its date is in the row\'s tooltip (founder 2026-10-15: "remove time")', () => {
    const rows = railRows([chat({ id: 'plain' })], [HELD], NOW);
    const v = render(<Rail {...base} rows={rows} />);
    const held = v.container.querySelector('[data-row-key="m:7"]') as HTMLElement;
    expect(held.textContent).not.toMatch(/held/i);
    expect(held.querySelector('[data-row-status]')).toBeNull();
    expect(held.querySelector('.vx-row2-meta')).toBeNull();
    expect(v.container.querySelector('[data-row-key="c:plain"] .vx-row2-meta')).toBeNull();
    expect(held.querySelector('[data-chat-row]')?.getAttribute('title')).toMatch(/\d/);
  });

  it('is marked as a call by its filled dot, a chat by a ring — no extra icon or word', () => {
    const v = render(<Rail {...base} rows={railRows([chat({ id: 'plain' })], [HELD], NOW)} />);
    expect(v.container.querySelector('[data-row-key="m:7"] .vx-rail-dot')?.getAttribute('data-kind')).toBe('meeting');
    expect(v.container.querySelector('[data-row-key="c:plain"] .vx-rail-dot')?.getAttribute('data-kind')).toBe('chat');
    const css = readFileSync(join(__dirname, '..', '..', 'ui-kit', 'layout', 'layout.css'), 'utf8');
    expect(css).toMatch(/\.vx-rail-dot:not\(\[data-tone\]\)\[data-kind="meeting"\]\s*\{\s*background:/);
  });

  it('a live meeting keeps its "live" word in the success colour and a success dot', () => {
    const v = render(<Rail {...base} rows={railRows([], [LIVE], NOW)} />);
    const row = v.container.querySelector('[data-row-key="m:8"]') as HTMLElement;
    const when = row.querySelector('.vx-row2-meta') as HTMLElement;
    expect(when.textContent).toBe('live');
    expect(when.hasAttribute('data-live')).toBe(true);
    expect(row.querySelector('.vx-rail-dot')?.getAttribute('data-tone')).toBe('success');
  });
});

describe('the row action takes the time\'s place on hover and focus (no overlap)', () => {
  const css = readFileSync(join(__dirname, '..', '..', 'ui-kit', 'layout', 'layout.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const rule = (sel: RegExp) => css.split('}').find((b) => sel.test(b.split('{')[0])) ?? '';

  it('the time steps aside on hover and on focus within the row', () => {
    const hide = css.split('}').filter((b) => /visibility:\s*hidden/.test(b)).map((b) => b.split('{')[0]).join(',');
    expect(hide).toMatch(/\.vx-rail \.vx-row2:hover \.vx-row2-meta/);
    expect(hide).toMatch(/\.vx-rail \.vx-row2:focus-within \.vx-row2-meta/);
  });

  it('the time column is at least as wide as the row action and right-aligned, so the action lands on it', () => {
    const meta = rule(/^\s*\.vx-rail \.vx-row2-meta\s*$/);
    expect(meta).toMatch(/min-width:\s*var\(--control-xs\)/);
    expect(meta).toMatch(/text-align:\s*right/);
    // the action is an xs IconButton overlaid at the row's right edge, on the row's hover colour
    expect(rule(/^\s*\.vx-rail \.vx-row2-actions\s*$/)).toMatch(/position:\s*absolute/);
  });

  it('the time never uses display:none (it keeps its space, so nothing shifts)', () => {
    expect(css).not.toMatch(/\.vx-rail[^{]*\.vx-row2-meta[^{]*\{[^}]*display:\s*none/);
  });
});
