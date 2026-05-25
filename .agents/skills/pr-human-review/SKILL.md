---
name: pr-human-review
description: Deliver a code review to a human as ONE GitHub single-commit URL containing only the pack's product files (no test/evidence/lockfile noise). Use whenever the user needs to do the actual human code-review gate (per the develop skill). Primary path is `scripts/squash-for-review.sh <pr> --worktree <path>` which creates a `codex/review-squash/<name>` side branch and prints the GitHub single-commit URL. Always favor this over per-file or per-commit links — one URL, complete diff, clean GitHub native view.
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

## Workflow — preferred (single-commit squash delivery)

This is the primary delivery path. Always offer it first.

1. Run `scripts/squash-for-review.sh <pr-number> --worktree <path> --name
   <slug>`. The script:
   - computes the non-test product files (via `list-non-test-files.sh`);
   - creates a side branch `codex/review-squash/<name>` rooted at the PR's
     base branch;
   - applies the product-only diff from the PR head as one squashed commit;
   - pushes to origin and prints the GitHub single-commit URL.
2. The reviewer opens that one URL (append `?diff=split&w=1` for split
   view + whitespace-ignored). GitHub renders the entire pack delta as a
   single commit — no 100+-file PR clutter, no per-commit overlap.
3. The reviewer reads top-to-bottom, leaves inline comments via the `+`
   icon on any line, and replies with the verdict in chat.
4. After the verdict lands, the develop skill writes
   `.agents/packs/<pack-id>/code-review.md` and flips the GitHub epic to
   `status:ready-for-stage`. This skill does NOT write the verdict file.

## Workflow — fallback (per-file local diff)

If the squash path can't be used (e.g. patch fails to apply cleanly
because the PR head has merge conflicts with base), fall back to per-file
local diffs:

1. Use `scripts/list-non-test-files.sh <pr-number>` to compute the
   non-test file list.
2. Generate one `.diff` per file under `/tmp/review/<pack-name>/`
   (e.g. `git diff <base>..HEAD -- <file>` per path).
3. Tell the reviewer to open the folder in VS Code; each `.diff` renders
   with syntax highlighting.
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
