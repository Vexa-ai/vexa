# (unset) — this workspace has not been set up yet

A **workspace** is a folder of durable memory: everything captured here — people, companies,
meetings, decisions, notes — lives as plain files, and **this README is its dashboard**: the
at-a-glance view the agent keeps current as a side effect of every write. It is a *view over the
files*, never a second source of truth.

> **First conversation sets this up.** What kind of workspace this becomes is decided by why it
> exists — the agent follows the matching playbook in [`flows/`](flows/README.md):
> **personal** (one person's memory) · **shared** (a group, a project, a meeting series) ·
> **global** (the organisation tier: what anyone in the org must know anyway).
> The setup conversation replaces this page with the real dashboard.

## Purpose

`(unset)` — one discriminating line: what this workspace is for. Routes writes when several
workspaces are mounted at once.

## Objective

`(unset)` — what winning looks like here, and by when. Meeting notes filed here are ranked against
it (what moved it, what blocks it). "No objective — reference folder" is a valid answer.

## Where things stand

`(unset)` — the running state against the objective: open commitments, blockers, next dates.

## What lives here

Entities under `kg/entities/<type>/`, one file each, shaped by the skeletons in
[`kg/templates/`](kg/templates/README.md). Dashboard sections (people · companies · meetings ·
follow-ups) appear here as the workspace fills in.

## Principles (how anything gets written here)

- **Dated and attributed** — entries carry when they were learned and from what source.
- **Never invent** — an unknown stays `(unset)` and says so; a guessed fact or link is worse than a gap.
- **Every name is a link** — to its entity file; a missing entity is a finding, not a formatting problem.
- **This page follows the files** — updated as a side effect of writes; if it can't be kept true, it says less.
