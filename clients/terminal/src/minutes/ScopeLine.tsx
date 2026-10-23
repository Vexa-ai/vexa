"use client";
/** THE PROFILE CHIP — the chat's profile and what it means, first ("Work · writes to biz · reads 8",
 *  "Work (changed) · …", "No profile · …", "Incognito · nothing is kept"), in the chat header (Vexa-ai/vexa#1783).
 *
 *  UI decision 6 (founder 2026-10-14): *"profile creation from chat, but profile choice is better
 *  here."* So clicking it opens a small menu — the person's profiles, each with a one-line summary and
 *  a check on the active one; picking one switches this chat from its next turn. "Create profile",
 *  "Edit this profile" and "Save as profile" START A CONVERSATION: a short human line in the chat and
 *  a hidden instruction to the agent, which asks what is needed, proposes the profile in one summary
 *  and makes it with its profile tools on yes. No per-workspace toggles: scope is set in the chat.
 *
 *  It is a MIRROR of the server: it re-reads whenever a scope verb answers (`surfaces/scopeEvents`). */
import { DESK_DISPLAY, deskName } from "./vocabulary";
import { featureOn } from "../platform/features";
import { Identity } from "../ui-kit/Identity";
import { useCallback, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Menu, type MenuItem } from "../ui-kit";
import {
  readProfiles, readScope, useChatProfile,
  type ChatProfile, type ScopeRow, type ScopeView,
} from "../data/workspaceApi";
import { SCOPE_CHANGED, notifyScope } from "../surfaces/scopeEvents";
import { ASK_CHAT_EVENT } from "../platform/events";
import "./minutes.css";

const DESK = "personal";

/** The chip's words when the server has not answered yet — never a number it did not give. */
export const PENDING_LINE = "Workspaces…";

/** What incognito does — and, honestly, what it does not do yet (the conversation itself is kept in
 *  the person's private system space until it is deleted on schedule). */
export const INCOGNITO_NOTE = "Incognito: no workspaces mounted, nothing written to your workspaces, never read by "
  + "another chat, left out of chat search, and deleted after the company's retention period. Until then "
  + "the conversation itself is kept in your private system space.";

/** The name to show: told apart from any other workspace called the same (whose · slug · empty). */
export const shownName = (w: ScopeRow) => w.display || w.name;

const title = (name: string) => name[0].toUpperCase() + name.slice(1);

function nameOf(v: ScopeView | null, slug: string | null): string {
  if (!slug) return "nowhere";
  if (slug === DESK) return featureOn("shell2") ? DESK_DISPLAY : v?.workspaces.find((w) => w.kind === "personal")?.name ?? deskName();
  const w = v?.workspaces.find((x) => x.slug === slug);
  return w ? shownName(w) : slug;
}

/** The hover text: every parameter of this chat's scope, read-only. */
export function scopeDetails(v: ScopeView): string {
  if (v.chat.profile === "incognito" && !v.chat.changed) return INCOGNITO_NOTE;
  const lines = [
    `Profile: ${v.chat.profile ?? "none"}${v.chat.changed ? " (changed in this chat)" : ""}`,
    `Saves to: ${nameOf(v, v.chat.target)}`,
    `Reads: ${v.chat.reads.length ? v.chat.reads.map((s) => nameOf(v, s)).join(", ") : "nothing"}`,
  ];
  const out = v.workspaces.filter((w) => w.state !== "hidden" && !v.chat.reads.includes(w.slug));
  if (out.length) lines.push(`Not read here: ${out.map(shownName).join(", ")}`);
  if (v.chat.note) lines.push(v.chat.note);
  return lines.join("\n");
}

/** The hidden instructions behind the menu's three conversation starters. The person sees only the
 *  short line; the agent gets the instruction. */
export const ASKS = {
  create: {
    say: "Create a chat profile",
    prompt: "[the person chose \"Create profile\" in the chat header] Help them create a chat profile. Ask, briefly and "
      + "one question at a time, what it is for, where its chats should write, what they should read, and whether it "
      + "should be incognito (no workspaces, nothing remembered). Then propose it in ONE short summary line and, "
      + "only after they say yes, create it with chat_profile_edit. Say in one line that it is now in the header menu.",
  },
  edit: (name: string) => ({
    say: `Edit my ${name} profile`,
    prompt: `[the person chose "Edit this profile" in the chat header] Help them change the "${name}" chat profile. `
      + "Read it with chat_profile_list, say in one line what it does now, ask what to change, propose the result in "
      + "ONE short summary line and apply it with chat_profile_edit only after they say yes.",
  }),
  save: (name: string | null) => ({
    say: "Save this chat's setup as a profile",
    prompt: "[the person chose \"Save as profile\" in the chat header] This chat's scope was changed by hand"
      + (name ? ` from the "${name}" profile` : "") + ". Read it with workspace_list, ask what to call the profile "
      + "(or whether to update the one it came from), propose it in ONE short summary line and save it with "
      + "chat_profile_edit only after they say yes.",
  }),
};

function ask(session: string, a: { say: string; prompt: string }): void {
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: false, display: a.say, session, prompt: a.prompt } }));
}

export function ScopeLine(p: {
  session: string;
  /** The chat's reads changed — the shell keeps its record of the chat's set in step. */
  onReads?: (reads: string[], view?: ScopeView) => void;
}) {
  const [view, setView] = useState<ScopeView | null>(null);
  const [profiles, setProfiles] = useState<ChatProfile[]>([]);
  const [defaultName, setDefaultName] = useState<string | null>(null);
  const [fault, setFault] = useState("");
  const { session, onReads } = p;

  const load = useCallback(async () => {
    try {
      const v = await readScope(session, false);
      setView(v);
      onReads?.(v.chat.reads, v);
    } catch { /* the chip keeps its last answer; it never invents one */ }
    try {
      const r = await readProfiles(session);
      setProfiles(Array.isArray(r.profiles) ? r.profiles : []);   // an answer without a list draws an empty menu
      setDefaultName(r.default);
    } catch { /* the menu shows what it had */ }
  }, [session, onReads]);

  useEffect(() => { setView(null); void load(); }, [load]);
  useEffect(() => {
    const again = () => { void load(); };
    window.addEventListener(SCOPE_CHANGED, again);
    return () => window.removeEventListener(SCOPE_CHANGED, again);
  }, [load]);

  if (view?.admin_account) return null;
  const active = view?.chat.profile ?? null;
  const pick = async (name: string) => {
    setFault("");
    try { await useChatProfile(name, session); }
    catch (e) { setFault(e instanceof Error ? e.message : "That did not switch."); }
    finally { notifyScope(); }
  };
  // LEAN (founder 2026-10-15): the menu's top is the DETAIL the chip leaves out — where it writes,
  // what it reads (by name), memory — then Incognito, then the person's own profiles, then Create.
  // No prebuilt profiles; defaults, edits and saving are said in the chat.
  const incognitoOn = active === "incognito";
  const detail: MenuItem[] = view ? [
    { key: "d:writes", disabled: true, data: { "data-scope-detail": "writes" }, onSelect: () => undefined,
      label: <span className="vx-scope-meta">Saves to {view.chat.target && <Identity kind="workspace" id={view.chat.target}
        slug={view.chat.target} name={nameOf(view, view.chat.target)} size={14} className="vx-id-inline" />}<b>{nameOf(view, view.chat.target)}</b></span> },
    { key: "d:reads", disabled: true, data: { "data-scope-detail": "reads" }, onSelect: () => undefined,
      label: <span className="vx-scope-meta vx-profile-summary">Reads {view.chat.reads.length
        ? view.chat.reads.map((s) => nameOf(view, s)).join(", ") : "nothing"}</span> },
    { key: "d:memory", disabled: true, data: { "data-scope-detail": "memory" }, onSelect: () => undefined,
      label: <span className="vx-scope-meta">Memory {incognitoOn ? "off" : "on"}</span> },
  ] : [];
  const incognito = profiles.find((pr) => pr.incognito);
  const own = profiles.filter((pr) => !pr.incognito);
  const profileItem = (pr: ChatProfile, first: boolean): MenuItem => ({
    key: `p:${pr.name}`, checked: active === pr.name, separatorBefore: first, data: { "data-profile": pr.name },
    // NAME OVER ITS SUMMARY — two lines, so neither is cut at the menu's width.
    label: <span className="vx-profile-item">
      <span className="vx-profile-name">{title(pr.name)}{defaultName === pr.name ? <span className="vx-scope-meta"> · default</span> : null}</span>
      <span className="vx-scope-meta vx-profile-summary">{pr.summary}</span>
    </span>,
    onSelect: () => { if (active !== pr.name) void pick(pr.name); },
  });
  const items: MenuItem[] = [
    ...detail,
    ...(incognito ? [profileItem(incognito, detail.length > 0)] : []),
    ...own.map((pr, i) => profileItem(pr, i === 0)),
    { key: "create", separatorBefore: true, data: { "data-ctx": "profile-create" }, label: "Create profile",
      onSelect: () => ask(session, ASKS.create) },
  ];
  return (
    <span className="vx-scope-chip-anchor" data-scope-line="" data-scope-profile={active ?? undefined}
      title={view ? scopeDetails(view) : undefined}>
      <Menu label="This chat's profile" variant="chip" align="end" items={items}
        triggerData={{ "data-scope-trigger": "" }}
        trigger={<><span className="vx-scope-text" data-scope-text="">{view?.line ?? PENDING_LINE}</span>
          <ChevronDown size={14} strokeWidth={1.75} aria-hidden /></>}
        footer={fault || undefined} />
    </span>
  );
}
