# The ORGANISATION tier (`_global`) — what it is, and where its setup lives

**This file is not the setup conversation.** The conversation lives in one place —
`_global/asks/setup-global.md`, read hot at click time, admin-editable, source at
`behavior/asks/setup-global.md`. This page exists only so an agent reading a workspace knows
what the tier IS and does not invent a second version of it.

It used to be that second version: a five-question, research-first, MDX-shaped org-onboarding script
with its own accept marker, seeded into every PERSONAL workspace, describing a conversation that no
longer works that way. Two specifications of one conversation do not produce a disagreement anyone
notices — they produce whichever one the agent happened to read.

## What `_global` is

The layer every agent in this company carries into every meeting, every brief and every mail.
Mounted READ-ONLY into every worker, on every turn. One admin edit changes how every agent in the
deployment behaves — for a bank that is the feature and the risk in the same breath — so it is
git-backed and only the instance admin can write it.

## It is THIN

Founder ruling, 2026-09-02:

> `_global` is not fully setup to become the global workspace — not a super thin layer. That might
> be pretty thin, BTW — so knowledge recombination is more achieved over workspace combination and
> not a static global dominant.

Five short files, and nothing else:

| file | what goes in it |
|---|---|
| `README.md` | the company's name as the first heading, then ONE sentence of what it does |
| `PRINCIPLES.md` | how this company works and what it refuses |
| `OBJECTIVES.md` | what it is trying to achieve in this period |
| `STRUCTURE.md` | the teams and who does what |
| `MISSING.md` | what is not yet known — the only file that gets more useful the more it admits |

**No company workspace. No org graph. No demo data.** The substance of the company lives in ordinary
workspaces — personal ones, and groups people are invited into — and a chat recombines knowledge by
mounting several of them. That mount stack is the mechanism; a fat `_global` is the thing it
replaces. Nobody is in anything they were not invited to.

`README.md`'s first two lines are load-bearing beyond `_global`: every agent in this deployment
introduces itself with the company name from that heading, so it goes out to that company's own
customers.

## The accept is a verb, not a marker

The setup conversation ends by calling `mark_global_ready`. That verb re-reads the five files, commits
them to `_global`'s history with the administrator as author, and lifts the instance gate. Until it
has accepted, this Vexa serves nobody: no other person can sign in, the flows engine parks every fact
instead of sending, and the operator verbs refuse by name.

Nothing may mark itself ready. Writing a `.scaffolded` file here does nothing — that marker belongs
to person and group onboarding, not to the organisation tier.
