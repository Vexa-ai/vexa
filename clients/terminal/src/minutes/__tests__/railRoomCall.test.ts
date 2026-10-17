/** A CALL STARTED IN A ROOM IS THE ROOM'S (app.dev 2026-10-17). The founder started a call in room
 *  #test; the rail then showed a second row, "Call with Vexa", next to the room, because every
 *  meeting no chat claims became its own row. agent-api stamps the room on the call's meeting row
 *  (`data.metadata.room`) and binds the meeting's chat to it, so the room's row is the only one. */
import { describe, it, expect } from 'vitest';
import { railRows, type Chat } from '../chats';
import type { MeetingMock } from '../../surfaces/meetingModel';

const NOW = Date.UTC(2026, 9, 17, 12, 0, 0);
const call = (over: Partial<MeetingMock> = {}): MeetingMock =>
  ({ id: '153', title: 'Call', status: 'past', live_status: 'completed', platform: 'vexa_call',
     native_id: 'call-803995f7c882', start_time: new Date(NOW - 5 * 60000).toISOString(),
     participants: [], ...over } as unknown as MeetingMock);
const room: Chat = { id: 's-0cd66f4b428c43d1b05fec3e49b469ed', label: 'test', workspaces: ['ai-tinkerers-lisbon-13143c'],
  artifacts: [], touched: true, createdAt: NOW - 600000, lastActivityAt: NOW - 1000 };

describe('a call started in a room', () => {
  it('gets no row of its own while the reader has the room', () => {
    const rows = railRows([room], [call({ room: room.id })], NOW);
    expect(rows.map((r) => r.key)).toEqual([`c:${room.id}`]);
  });

  it('keeps its row when the reader does not have that room, and a call outside any room keeps its row', () => {
    expect(railRows([], [call({ room: room.id })], NOW).map((r) => r.key)).toEqual(['m:153']);
    expect(railRows([room], [call()], NOW).map((r) => r.key).sort()).toEqual([`c:${room.id}`, 'm:153'].sort());
  });
});
