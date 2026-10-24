---
name: vexa-workspace
description: Keep the person's (or the team's) Vexa workspace current — record people, companies, decisions, numbers, deadlines and commitments from calls and chats into the workspace Claude writes to, by that workspace's own rules. Use after a call ends, when the person asks to note, file, remember or look something up, and before writing anything to a Vexa workspace or the local ~/Vexa folder.
---

# Vexa workspace

A Vexa workspace is a git repo of markdown pages that people and their agents share. Every teammate's Claude writes into the same workspace by the same rules, so the team's knowledge grows by itself. The session context says where you write (`vexa_settings`): a cloud workspace, a local folder, or only when asked. If it says nothing is chosen, ask first (vexa-setup skill) and write nothing.

## 1. Read the workspace's rules before writing — they win over this skill
- `workspace_purpose(slug)` — what the workspace is for.
- `workspace_read(slug, "CLAUDE.md")` — its conventions (the layout below is the default one; a team may have changed it). Also any page it points to for rules.
- Local folder: read `<folder>/CLAUDE.md` if it exists.

## 2. Search first, extend what exists
`workspace_search(q)` for the person, company or topic before creating anything. Extend the page you find; don't start a second one.

## 3. Write by the default conventions (from the workspace template's CLAUDE.md)
- `entity_upsert(kind, name, facts, source)` is the whole write for a person, company, meeting, project or decision: it creates `kg/entities/<kind>/<slug>.md` with its frontmatter or appends a dated entry.
- **A name without a page gets one now** — except a name heard once in a transcript with no other source: that goes to the meeting note's open questions («Who is X?»), since transcription mishears names.
- **Every fact carries its source** (the meeting link, the chat, the file). No source, no fact.
- **Gaps go to `kg/MISSING.md`**, never invented.
- Pages start with frontmatter `type`, `id` (the slug), `title`; keep `summary:` (≤280 characters) current on people, companies and projects.
- Link with `[[wikilinks]]`; name a meeting as `[[meeting:<meeting_id>]]`, never by its notes filename.
- Every write is a commit in the person's name marked `Written-by: agent` (the server adds it).

## 4. After a call
1. `get_meeting_transcript` (and the participants from the speaker labels).
2. The meeting page: `meeting_bind_workspace(meeting_db_id, workspace)` creates and attaches it (shared workspace: members get it too). Fill it: a short summary, decisions, numbers, deadlines, commitments with owners, open questions.
3. `entity_upsert` each person and company that matters, linking them to the meeting page.
4. Reply in chat with ONE line and the link to the meeting page.

## Local folder
Same layout and rules, written with the file tools under the chosen folder (`kg/entities/<kind>/<slug>.md`), committed with git if the folder is a repo.

## Never
Personal or family talk, health, gossip, passwords, keys, tokens, card numbers — not even in a summary. Don't write into a workspace the person didn't choose, and don't move the write target yourself (ask them; `vexa_settings` saves their answer).
