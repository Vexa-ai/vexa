/** A CHAT'S NAME NEVER SHOWS THE USER-INPUT MARKER (Vexa-ai/vexa#1641).
 *
 *  The dogfood rail and the delete dialog showed a row called
 *  `<!--vexa:user-input-below-->zz-delete-repro-8…`: the composer's grounding/user boundary marker,
 *  taken into the name. The server now names a row from the words after it
 *  (`core/agent/tests/test_chat_title_marker.py`); this pins the client's half — a label from a
 *  server that still sends the marker, or one already sitting in this browser, reads without it.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  chatsFromSessions, isPlaceholderLabel, mergeChats, nameChat, newChat, railRows, stripMarkers,
  titleFromTurn, type Chat, type ServerSession,
} from "../chats";

const NOW = Date.parse("2026-10-11T12:00:00Z");
const MARKED = "<!--vexa:user-input-below-->zz-delete-repro-8 please remove this ch…";
const CLEAN = "zz-delete-repro-8 please remove this ch…";

const session = (over: Partial<ServerSession> & { session: string }): ServerSession => ({
  title: null, label: null, created: NOW / 1000 - 60, last_active: NOW / 1000 - 30,
  workspaces: null, scaffold: null, touched: true, ...over,
});

beforeEach(() => {
  localStorage.clear();
});

describe("the user-input marker never reaches a label", () => {
  it("strips any vexa marker, whole or cut off at the end", () => {
    expect(stripMarkers(MARKED)).toBe(CLEAN);
    expect(stripMarkers("hello <!-- vexa:x -->world")).toBe("hello world");
    expect(stripMarkers("hi <!--vexa:user-inp")).toBe("hi");
  });

  it("a server label carrying the marker reads without it", () => {
    const [c] = chatsFromSessions([session({ session: "pchat-a", label: MARKED, title: MARKED })], NOW);
    expect(c.label).toBe(CLEAN);
  });

  it("an older server that sends only the raw title reads without it", () => {
    const [c] = chatsFromSessions([session({ session: "pchat-b", title: MARKED })], NOW);
    expect(c.label).toBe(CLEAN);
  });

  it("a marked label stored in this browser is shown clean on the rail, and is not rewritten", () => {
    const stored: Chat = { ...newChat(MARKED, ["personal", "_global"], { id: "pchat-c", now: NOW }), touched: true };
    const [row] = railRows([stored], [], NOW).filter((r) => r.chatId === "pchat-c");
    expect(row.label).toBe(CLEAN);
    expect(stored.label).toBe(MARKED);
  });

  it("a marked local label yields to the server's clean one in the merge", () => {
    expect(isPlaceholderLabel(MARKED)).toBe(true);
    const local: Chat = newChat(MARKED, ["personal", "_global"], { id: "pchat-d", now: NOW });
    const server: Chat = newChat(CLEAN, ["personal", "_global"], { id: "pchat-d", now: NOW });
    expect(mergeChats([local], [server]).find((c) => c.id === "pchat-d")?.label).toBe(CLEAN);
  });

  it("a first turn names a chat with the person's words only", () => {
    expect(titleFromTurn("<!--vexa:user-input-below-->plan the launch")).toBe("plan the launch");
    const draft = newChat("New chat", ["personal", "_global"], { id: "pchat-e", now: NOW });
    expect(nameChat(draft, "<!--vexa:user-input-below-->plan the launch").label).toBe("plan the launch");
  });
});
