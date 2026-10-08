# The ORGANISATION tier (`_global`) — what it is, and that it is optional

**`_global` starts empty of company data, and it may stay that way.** Founder ruling, 2026-10-08:
*"let's remove global setup at all so that there is no need to setup global at all - let it be empty
with no data - it's fine."* (The image seeds only machinery into it — asks, mail templates, flow
pages, `POLICIES.md` — plus unwritten placeholders for the five files below; none of it names a
company, and an empty directory works just as well.) Nothing waits for it: every person who signs in is served from their first sign-in,
flows send, and no verb refuses because the company layer is unwritten. Never tell anyone the
instance is "not set up" for want of it, and never push an administrator into writing it.

**This file is not the conversation that writes it.** That conversation, for an administrator who
chooses to, lives in one place — `_global/asks/setup-global.md`, read hot at click time,
admin-editable, source at `behavior/asks/setup-global.md`. This page exists only so an agent
reading a workspace knows what the tier IS and does not invent a second version of it.

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

## Writing it is optional; accepting it is a verb

When an administrator does write the layer, the conversation ends by calling `mark_global_ready`.
That verb re-reads the five files and commits them to `_global`'s history with the administrator
as author. It gates nothing: until it runs — or if it never runs — the instance serves everybody
exactly as before, and the files that ship as unwritten placeholders simply say nothing about the
company.

Nothing may mark itself accepted. Writing a `.scaffolded` file here does nothing — that marker
belongs to person and group onboarding, not to the organisation tier.
