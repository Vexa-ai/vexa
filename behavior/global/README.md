# Company

<!-- vexa:unwritten — the setup conversation replaces the heading above with this company's NAME
     and this comment with ONE sentence of what it does. Those two lines are read out loud to
     strangers ("I'm Vexa, the meeting assistant at <company>"), so `mark_global_ready` refuses a
     heading that is still the placeholder, and refuses a file that still carries this marker. -->

The organisation tier: the few short files every agent in this company carries into every meeting,
every brief and every mail. It is THIN by construction — the substance of the company lives in
ordinary workspaces that a chat recombines by mounting several of them. Nothing here is a place for
meeting notes, customer records or documents.

## The map

| file | what it holds |
|---|---|
| [`README.md`](README.md) | this map — the company's name, one sentence of what it does, then the links |
| [`PRINCIPLES.md`](PRINCIPLES.md) | how this company works and what it refuses |
| [`OBJECTIVES.md`](OBJECTIVES.md) | what it is trying to achieve in this period |
| [`STRUCTURE.md`](STRUCTURE.md) | the teams, who does what, and who can see what |
| [`MISSING.md`](MISSING.md) | what is not yet known — the only file that gets more useful the more it admits |
| [`POLICIES.md`](POLICIES.md) | the rules: who may do what to what, with this deployment's answers |
| `asks/` | the presets a link opens a chat on. The image tops them up; an admin edits them here |
| `mail/` | the words this deployment mails, as files. Edit one and the next mail carries it |
| [`flows/`](flows/README.md) | one page per flow, generated from the code that runs it — trigger, steps, mails, writes, the rules it honours, and the Python at the foot |

## How this file gets written

Everything above the map is the setup conversation's to fill and yours to correct. Everything below
it is structure: this directory arrives scaffolded, and the conversation fills blanks and confirms
them. It never composes the structure itself, because a layer whose shape depends on how one chat
went is a layer nobody can review.

`_global` is a git repository. Every acceptance is a commit authored by the administrator who made
it, so a change to what every agent in this company carries is reviewable, diffable and revertable.
