---
name: pr-human-review
description: Open a GitHub PR in split-view, filtered to non-test product files, for a human reviewer to read the diff line by line and leave inline comments. Use when the user needs to do the actual human code-review gate (per the develop skill) — not the automated finder pass. The skill computes the non-test file list, opens the PR Files page with split view + whitespace-ignored, and walks the human through each product file in order.
---

# PR Human Code Review

## Purpose

Help a human reviewer do the actual code-review gate on a Vexa pack PR. The
develop skill requires a human verdict in `code-review.md` — this skill is the
companion for the reviewer doing the read-through.

Scope: only **non-test, non-evidence product files**. Test files, evidence
files under `.agents/packs/`, and lockfiles are filtered out — they're noisy
for an inline read-through and the human can return to them later if needed.

## Required Inputs

- PR number (or PR URL) and the owner/repo it lives in.
- The base branch (usually the pack's base, e.g. `v0.10.6`).
- Optional: the local worktree path for the PR head branch (if the human wants
  the script to use local `git diff` instead of `gh pr diff`).

## Workflow

1. Use `scripts/list-non-test-files.sh <pr-number>` to compute the non-test,
   non-evidence file list for the PR (uses `gh pr diff --name-only` then
   filters out paths matching the patterns in
   `references/test-file-patterns.txt`).
2. Use `scripts/open-split-view.sh <pr-number>` to launch the PR Files page
   in the user's browser with `?diff=split&w=1`. This is the canonical
   "split view, ignore whitespace" mode for line-by-line reading.
3. Print the non-test file list as a numbered navigation plan. The reviewer
   uses GitHub's `t` shortcut (file finder) or the left file tree to click
   through them in order.
4. For each file, the reviewer:
   - reads the diff in split view;
   - hovers a line number → `+` icon → leaves an inline comment if needed;
   - clicks `Start a review` on the first comment, then `Add review comment`
     for subsequent comments.
5. When done, the reviewer clicks top-right `Submit review` and chooses one of
   `Approve`, `Comment`, or `Request changes`.
6. The reviewer then records their verdict in
   `.agents/packs/<pack-id>/code-review.md` with:
   - reviewer identity (email or GitHub handle);
   - timestamp;
   - verdict (`pass` / `pass with notes` / `changes requested` / `block`);
   - notes covering each blast-radius surface from the pack epic;
   - confirmation that the diff stays within the pack scope.

The `code-review.md` write step is **never** done automatically — only the
human reviewer authors that verdict. This skill prepares the read-through
material; it does not grant the verdict.

## File filtering

Non-test files = all changed paths in the PR diff EXCEPT:

- paths containing `/tests/`, `/__tests__/`, `/test_/`, `/spec/`
- file names starting with `test_` or ending with `_test.py`, `.test.ts`,
  `.test.tsx`, `.test.js`, `.spec.ts`, `.spec.tsx`, `.spec.js`
- paths under `.agents/packs/` (pack evidence)
- lockfiles: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`,
  `poetry.lock`, `uv.lock`, `Cargo.lock`, `go.sum`
- compiled artifacts: `*.lock`, `dist/`, `build/`, `.next/`, `node_modules/`

Patterns are kept in `references/test-file-patterns.txt` so they can be
updated without changing the script.

## Output

Print a numbered list of non-test files to the reviewer, then a final
markdown table of every file with:

- file path
- diff-stat (`+a -b` lines)
- direct GitHub anchor URL to the file in the PR Files split view

The reviewer reads the table top-down; ordering is by file path so related
files in the same module land adjacent.

## Browser interaction

If the user has the claude-in-chrome MCP available, the orchestrator script
can post the navigation plan AND drive the browser to the PR Files page. If
not, it prints URLs for the user to open themselves.

## Out of scope

- Do NOT post automated review comments on the PR. That is a separate
  finder-sweep activity (the `develop` skill's pre-review draft), not this
  skill. This skill is strictly for the human reviewer's read-through.
- Do NOT mark the GitHub epic as `status:ready-for-stage`. That belongs to
  whatever skill orchestrates the develop → ready-for-stage transition.
- Do NOT write or modify `code-review.md`. Only the human reviewer writes
  that file with their explicit verdict.

## Completion response

When the human signals their verdict (e.g. `pack4 code-review: pass`),
record the verdict in `.agents/packs/<pack-id>/code-review.md` via the
`develop` skill's evidence-write path. This skill itself only prepares
the read-through.
