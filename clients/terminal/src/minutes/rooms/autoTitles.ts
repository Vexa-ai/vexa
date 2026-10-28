"use client";
/** The sidebar never shows "Chat" or "Chat 12:30" (home2): a row whose name nobody chose takes the
 *  chat's first human sentence — the same rule as `titleFromTurn` — read from its history once and
 *  remembered for the session. An unnamed chat nobody wrote in is not listed (the open one stays). */
import { useEffect, useState } from "react";
import { isPlaceholderLabel, titleFromTurn } from "../chats";
import { sessionHistory } from "../../surfaces/sessionsApi";

const GENERIC = /^chat( \d{1,2}:\d{2})?$/i;
export const needsTitle = (label: string) => isPlaceholderLabel(label) || GENERIC.test((label ?? "").trim());
const known = new Map<string, string>();
const asked = new Set<string>();

export function useAutoTitles<T extends { chatId: string | null; label: string }>(rows: T[], on: boolean, keep?: string | null): T[] {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const want = rows.filter((r) => r.chatId && needsTitle(r.label) && !asked.has(r.chatId)).slice(0, 40);
    if (!want.length) return;
    let live = true;
    for (const r of want) asked.add(r.chatId!);
    void Promise.all(want.map((r) => sessionHistory(r.chatId!).then((h) => {
      const first = (h.turns ?? []).find((t) => t.role === "user");
      known.set(r.chatId!, first ? titleFromTurn(String(first.text ?? "")) : "");
    }).catch(() => undefined))).then(() => { if (live) tick((x) => x + 1); });
    return () => { live = false; };
  }, [rows, on]);
  if (!on) return rows;
  return rows
    .filter((r) => !(r.chatId && needsTitle(r.label) && known.get(r.chatId) === "" && r.chatId !== keep))
    .map((r) => (r.chatId && needsTitle(r.label) && known.get(r.chatId) ? { ...r, label: known.get(r.chatId)! } : r));
}
