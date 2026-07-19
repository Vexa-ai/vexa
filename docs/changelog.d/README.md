# `docs/changelog.d/` — per-PR changelog fragments (the towncrier pattern)

**Do not edit `docs/docs/changelog.mdx` directly for a per-PR entry.** Drop a *fragment* here
instead. This exists to kill the merge-train tax: when N parallel PRs each append a line to the
single tail of `changelog.mdx`, they collide on the same last line even though the additions never
interact (the v0.12.9 batch paid 5 manual conflict-resolution cycles to exactly this). One file per
PR means N PRs create N distinct files — git auto-merges them, zero conflicts.

## Add a fragment (in your PR)

Create **one file per PR**, named `<pr>-<slug>.md` — e.g. `692-fragments-collector.md`. (Don't know
the PR number yet? Use the issue number, or rename on push. Any unique `<n>-<slug>.md` works; the
collector orders by the leading number.)

Its contents are the changelog entry exactly as it should read — one or more Markdown bullets, in
the same voice as the existing `## 0.12.x maintenance fixes` bullets:

```markdown
- **Short subject: what changed and who feels it (#692).** One or two sentences of the user-visible
  effect. Link the relevant docs page if there is one. See [Deployment](/deployment).
```

That fragment file **is** your PR's docs touch — it satisfies `docs-current` (D6c / ADR-0032) for a
user-visible change on its own; you don't also need to edit `changelog.mdx`.

Not every PR needs a fragment: repo-tooling / docs-only / test-only changes with no user-visible
effect don't add a changelog line (they take `docs: none` or ride their own docs). Add a fragment
only when a `:v012` user would want to read about the change.

## What happens at release

The release version-bump runs the collector once:

```bash
node scripts/changelog-collect.mjs            # fold pending fragments into changelog.mdx, remove them
node scripts/changelog-collect.mjs --check    # preview only (exit 3 if fragments are pending)
```

It appends every pending fragment into the `## <MAJOR>.<MINOR>.x maintenance fixes` section of
`docs/docs/changelog.mdx`, then deletes the consumed fragments so this directory returns to empty
(just this README). It never touches the `docs-reflects:` stamp — the version-bump advances that
separately so `gate:docs-version` stays green. See [`releases/README.md`](../../releases/README.md).

Migrate nothing retroactively — the convention starts now; existing `changelog.mdx` sections stay
as they are.

## The fragment states what the WITNESS can see — not what the wiring promises

A fragment's bullet is release-notes truth. If your PR `Refs` its issue (instead of `Closes`)
because a live acceptance leg remains open, the fragment must claim the **mechanism you proved**
("hints now reach the transcriber"), never the **end value still awaiting its human bar**
("segments carry who spoke"). The v0.12.14 witness pass caught exactly this: two fragments
advertised live speaker attribution, the walk showed `seg_N` — and the release notes on `main`
had to be corrected after the fact. The bundle was honest; the headline wasn't. Match them.
