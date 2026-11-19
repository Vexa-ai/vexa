/** Where a voice note starts (founder 2026-10-13: "record voice note should be just an MCP tool and
 *  an item in the hints here"). Two ways only: the empty chat's "Record a voice note" chip, and the
 *  agent's `record_voice_note` tool, whose call opens the recorder in this tab (the agent has no
 *  microphone). The rail no longer carries a mic; while a note records, the rail still shows it is
 *  live and can stop it. The composer's mic is dictation into the text box, not a voice note. */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { toolUiEvent } from "../../surfaces/toolUiEvents";
import { VOICE_NOTE_OPEN } from "../voiceNote";
import { CONNECTIONS_OPEN } from "../connectionEvents";

const SRC = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(SRC, p), "utf8");

describe("the agent's tool opens the recorder", () => {
  it("record_voice_note, under any MCP prefix, is the open-recorder event", () => {
    expect(toolUiEvent("record_voice_note")).toBe(VOICE_NOTE_OPEN);
    expect(toolUiEvent("mcp__vexa__record_voice_note")).toBe(VOICE_NOTE_OPEN);
  });
  it("connection_request still opens Connections, and other tools open nothing", () => {
    expect(toolUiEvent("mcp__vexa__connection_request")).toBe(CONNECTIONS_OPEN);
    expect(toolUiEvent("list_meetings")).toBeNull();
    expect(toolUiEvent("record_voice_note_later")).toBeNull();
  });
  it("the chat forwards a tool's event through the one table", () => {
    expect(read("surfaces/chat.tsx")).toMatch(/toolUiEvent\(tool\)/);
  });
  it("the shell answers the event by starting the recorder", () => {
    expect(read("minutes/MinutesShell.tsx")).toMatch(/addEventListener\(VOICE_NOTE_OPEN/);
  });
});

describe("no dedicated start button", () => {
  it("the rail has no idle mic", () => {
    const rail = read("minutes/Rail.tsx");
    expect(rail).not.toMatch(/label="Record a voice note"/);
    expect(rail).not.toMatch(/onVoiceNote/);
    expect(read("minutes/MinutesShell.tsx")).not.toMatch(/onVoiceNote=/);
  });
  it("the composer mic is dictation, not a voice note", () => {
    const chat = read("surfaces/chat.tsx");
    expect(chat).toMatch(/startStreamingDictation/);
    expect(chat).not.toMatch(/startVoiceNote/);
  });
});

describe("a voice note is the person's own voice", () => {
  it("the shell starts it under the signed-in person's name, never the bot's generic Speaker", () => {
    expect(read("minutes/MinutesShell.tsx")).toMatch(/startVoiceNote\(browserVoiceNoteDeps\(\), \{ speakerName: /);
  });
});
