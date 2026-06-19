# The Gates: How to Contribute to Vexa Without Knowing How Vexa Works

*What Vexa's CI checks, and why — for your first pull request.*

Open a pull request to Vexa and eight automated checks run against your change. They're called **gates**. Each guards one rule of the codebase. You don't need to know the rules up front: break one and the gate names it and points at the file. Green means your code fits and won't break anything — then a human reviews the idea.

## What CI is

CI runs your change on a clean machine every time you push — not your laptop, where "works on my machine" quietly depends on things you forgot you installed. In Vexa it's one command:

```bash
node scripts/gates.mjs all      # or: pnpm gates
```

Run it locally before you push. It's the exact thing CI runs.

## What a gate is

One check, one yes/no question. Vexa has eight, run in sequence. The house rule:

> **Code counts only when it's gate-green** — not when it's written, not when it works for you.

## The eight gates

| Gate | Checks | Why it matters |
|---|---|---|
| `readme` | every folder has a non-empty `README.md` | a folder that can't say what it's for becomes a junk drawer |
| `exports` | every library declares its public surface | nothing reaches into a package's internals, so you can rearrange the inside freely |
| `isolation` | a brick imports only its own files, Node built-ins, and declared deps | keeps modules independent — liftable, testable, replaceable on their own |
| `graph` | no dependency cycles; no forbidden cross-domain imports | cycles make code impossible to untangle; the seams keep domains separable |
| `schema` | contracts and their example messages match | a contract is a promise between two parts; this stops the two sides drifting apart |
| `python` / `node` | it builds and the unit tests pass | the floor — nothing else matters if it doesn't build |
| `licenses` | every dependency's license is on the allowed list | one wrong-licensed dependency can legally compromise the whole project |

None of these judge whether your idea is good. They check whether your code **fits**.

## The point: the gates are the architecture

Vexa is built as small bricks behind contracts. In most projects the rules of that design live in people's heads — you learn them from a stale wiki or from review nitpicks. Vexa encodes them as these eight gates. So you don't study the architecture to contribute correctly; the gates carry it and tell you exactly where you stepped out of line.

## Your first PR

1. Fork, branch off `main`.
2. `pnpm install`. It reads `package.json` (rough version ranges) and the **lockfile** `pnpm-lock.yaml` (exact pinned versions, so everyone installs an identical tree). **Add a dependency → commit the updated lockfile too** — CI installs with `--frozen-lockfile` and fails if the two disagree.
3. Write the change. New brick? Give it a README and a front door. Existing brick? Stay in its lane.
4. `pnpm gates` locally — catch problems in seconds, not a CI round-trip later.
5. Push, open the PR. The eight gates run on a clean machine.
6. Red? The message names the gate, the file, and the rule. Fix, push.
7. Green? A maintainer reviews the part machines can't: is this the right idea?

## A red gate isn't a rejection

It's a house rule you couldn't have known, with the fix in the message. `missing README: meetings/modules/my-feature/` is a to-do item, not a verdict.

## What the gates don't do

Green is necessary, not sufficient. They prove your code fits; they can't prove it's right. A maintainer judges the idea, and the live tests (does the bot actually transcribe a real meeting?) need credentials, so they run maintainer-side before merge. Green means "structurally sound, ready for human review."

## The mental model

> **The gates are a contract between you and the codebase. Follow them and your code fits, automatically — which frees humans to care about your ideas, not your indentation.**

Push, read what the gates say, fix what they point at. The project's rules are encoded so the machine remembers them for you.

Run `pnpm gates`, send the PR.
