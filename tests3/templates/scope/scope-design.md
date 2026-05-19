# Scope Design — <release_id>

Status: draft
Stage: `scope-design`

This is the concise, human-centered start of the release:

```text
why -> what -> how
```

Keep this readable in 3-5 minutes. The goal is not to prove the release yet;
the goal is to make the release intent legible enough to expand.

## Source Signals

List only signals that shaped this release. Every item needs a link/path and a
one-line reason so the human does not have to reverse-engineer relevance.

| Signal | Why it matters |
|---|---|
| [<issue/pr/log/doc/customer signal>](<url-or-local-path>) | <one-line relevance> |

## Why

<Why does this release exist now? Name the customer/product/engineering pain in
plain language.>

## What

In scope:

1. <item or pack>
   - [<reference>](<url-or-local-path>) — <one-line reason>

Out of scope:

1. <item or pack>
   - [<reference>](<url-or-local-path>) — <why it is excluded/deferred>

## How

Design stance:

- <constraint or principle that should shape implementation>
- <what kind of fix is preferred>
- <what must not be hidden as a fallback/workaround>
- <what belongs to machine proof vs human judgment>

## Human Decisions

Accepted:

- <decision>

Open:

- <decision still needed before/inside scope-deliver>

## Exit

`scope-design` is complete when the human can answer:

- Why does this release exist?
- What is in and out?
- What design stance constrains implementation?
- What must be expanded by `scope-deliver`?

