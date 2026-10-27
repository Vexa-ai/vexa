---
name: workspace
description: Use the person's Vexa workspace well around their calls — read it before a call for who is in it, the history and open items; while and after a call, record every person, company, project and decision as a linked card and keep the meeting's page as the call's live report, in small updates, never touching what the person wrote; say where you wrote. Use whenever you write to or read from a Vexa workspace about a meeting, a person or a company.
---

# The Vexa workspace, around calls

The workspace is the person's memory of their calls: a card per person, company, project and
decision, and a page per meeting. Keep it the way a careful assistant keeps a notebook.

**Before a call.** Read what is already there: `workspace_search` for the people, the company and
the topic; `workspace_read` the cards it finds. Use their history, open questions and commitments
when you prepare the person or answer during the call.

**Cards, never prose-only notes.** For every person, company, project and decision a call names,
`entity_upsert` it (one call each): `kind`, `name`, a one-line `summary`, `facts` with what this
call added, `source` = the meeting. Link both ways with `[[Name]]`: the person's card names
`[[Company]]`, the company's card lists `[[Person]]`, the decision names who decided and the project
it belongs to. Do it on every listener wake that brings new names or decisions, not only at the end.

**No duplicates.** Search for the name first (`workspace_search`) and upsert the page that exists,
under its own name. One edge per pair: a second relation to the same page goes into the same
`connections` entry («asked to check; owns»), never a second one. Before adding a fact, read the
card: skip anything it already says in other words, and when the call adds a detail, send the fuller
fact once. The demo call's speakers (Maya, Leo) and other scripted voices are not people: never give
them pages.

**«In this call»: the call's live summary on its page.** What it is, its shape and when to rewrite
it are the page `_global/asks/in-this-call.md` (`workspace_read` slug `_global`): follow it whenever
Vexa Live wakes you with «[Vexa · in this call]», and answer those wakes with «·» and no chat message.

**The meeting's page is the call's live report.** Find it with `get_meeting` (its attached page);
if there is none, write `meetings/<YYYY-MM-DD>-<title>.md` with `workspace_write` and attach it with
`meeting_attach_page`. Write only inside its regions — `<!-- meeting:decisions:start/end -->`,
`commitments` (who, what, by when), `people` (with `[[links]]`), `questions`, `report` after the
call, and the `## In this call` section. Everything outside them is the person's: above all the block from
`<!-- vexa:human-notes:start -->` to `<!-- vexa:human-notes:end -->`, which you never touch, and the
transcript slot.

**After a call, or when a page is thin** (a short summary, few facts, open questions): if you can read
the person's mail — a Gmail/Google connector in this chat, or Vexa's `mail_*` tools — search it for
the name or the company's domain over the last 180 days (e.g. `"Ivan Osman" OR infobip.com newer_than:180d`),
read what matters, and add the facts with the thread as their source. Read only; never send or draft.
Before a call, the same search for the participants tells you what is open with them.

**When a call ends** (or «Summarize»): pages first, then the text. 1) `entity_upsert` the project or
product, each person, each company and every decision (`kind: decision`), the call as the source. 2) A
real title for the call (≤ 6 words, the topic) with `annotate_meeting` and on its page. 3) The summary,
decided, committed and open questions with `section_update`, every name a `[[link]]` to its page.
4) `links_report`, then `links_update` with its fixes, until the score reaches the target. A summary
with no links is not done.

**Small updates.** Change the region that changed, with what is new; don't rewrite the page or a
card from scratch. Keep the person's wording where they edited yours.

**Say where you wrote**, in one line after a batch of updates: «Updated [[Acme]], [[Dana Lee]] and
the meeting page (decisions, commitments).»
