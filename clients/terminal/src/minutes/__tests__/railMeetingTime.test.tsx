/** A MEETING ROW SHOWS ITS TIME, NOT "held" (founder, app.dev 2026-10-11, on a screenshot of the rail
 *  where meeting rows read "Campbell  Held  ⋯" and, on hover, "Hel⋯": *"i think this 'held' does not
 *  make sense anymore"*).
 *
 *  Pinned: a finished meeting's row carries no status word and shows its date in the time column
 *  like every chat row; a live meeting keeps its accent "live" word; and the ⋯ never draws over the
 *  time — on this rail it has its own column, reserved on every row, so nothing shifts on hover. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
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

const HELD = meeting('7', 'Campbell', 'completed', -2 * 24 * 60);       // two days ago: a weekday
const LIVE = meeting('8', 'Bot into Campbell Zoom call', 'active', -5);

describe('a meeting row in the chat list', () => {
  afterEach(() => cleanup());

  it('a finished meeting shows no "held" — its date, in the same column and format as a chat row', () => {
    const rows = railRows([chat({ id: 'plain' })], [HELD], NOW);
    const v = render(<Rail {...base} rows={rows} />);
    const held = v.container.querySelector('[data-row-key="m:7"]') as HTMLElement;
    expect(held.querySelector('[data-chat-row]')?.textContent).not.toMatch(/held/i);
    expect(held.querySelector('[data-row-status]')).toBeNull();
    const when = held.querySelector('[data-row-when]') as HTMLElement;
    expect(when.textContent).toBe(rows.find((r) => r.key === 'm:7')!.whenLabel);
    expect(when.textContent).not.toBe('');
    expect(v.container.querySelector('[data-row-key="c:plain"] [data-row-when]')?.textContent).not.toBe('');
  });

  it('a live meeting keeps its "live" word in the accent colour', () => {
    const v = render(<Rail {...base} rows={railRows([], [LIVE], NOW)} />);
    const when = v.container.querySelector('[data-row-key="m:8"] [data-row-when]') as HTMLElement;
    expect(when.textContent).toBe('live');
    expect(when.style.color).toBe('var(--accent)');
  });

  it('the ⋯ has its own column on every row, so it never draws over the time', () => {
    const v = render(<Rail {...base} rows={railRows([chat({ id: 'plain' })], [HELD], NOW)} />);
    for (const key of ['m:7', 'c:plain']) {
      const r = v.container.querySelector(`[data-row-key="${key}"]`) as HTMLElement;
      const main = r.querySelector('[data-chat-row]') as HTMLElement;
      const act = r.querySelector('.vx-row-act') as HTMLElement;
      expect(act.style.position).toBe('absolute');
      // the ⋯ is 14px of icon + 4px padding a side, 3px from the edge: 25px, inside the 26px reserve
      expect(parseInt(main.style.paddingRight, 10)).toBeGreaterThanOrEqual(3 + 4 + 14 + 4);
    }
  });
});
