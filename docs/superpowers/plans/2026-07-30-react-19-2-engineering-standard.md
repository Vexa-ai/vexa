# React 19.2 Engineering Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement
> this plan task-by-task. The repository-local no-commit constraint overrides workflows that
> require per-task commits. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a repository-specific React 19.2 engineering standard for
`clients/terminal` that gives authors and reviewers enforceable rules, honest current
automation status, and a staged path to ESLint, React Compiler, test, and CI enforcement.

**Architecture:** One canonical Mintlify page owns the rules and examples. Navigation,
architecture documentation, and Terminal README files only link to that page; they do not
duplicate its normative content. Stable rule IDs connect the rule catalog, review matrix,
checklists, rollout plan, and exception records.

**Tech Stack:** React 19.2.7, React DOM 19.2.7, Next.js 15.5.19 App Router, TypeScript
5.9.3, Vitest 4.1.9, Testing Library React 16.3.2, Mintlify MDX, pnpm 11.7.0.

## Global Constraints

- Scope the standard to React code under `clients/terminal`; do not claim it governs
  non-React backend packages.
- Treat the exact lockfile runtime as React/React DOM 19.2.7 while recording that
  `package.json` currently declares `^19.1.0`.
- Server Components are applicable because the App Router uses them; Server Actions are
  supported but currently absent, so they are conditional guidance rather than a mandate.
- React Compiler is not installed or enabled. Next 15.5 exposes the capability through
  `experimental.reactCompiler`; describe an opt-in pilot, never current enforcement.
- No checked-in ESLint configuration or dependency exists, and CI does not run lint.
  Distinguish current checks from proposed checks in every automation claim.
- Do not add dependencies, enable Compiler, change CI, or modify runtime code in this task.
- Do not mass-condemn or mass-refactor legacy code. Separate removed/incompatible APIs,
  demonstrated defects or performance loss, architectural risk, and old-but-valid style.
- Apply DRY and SOLID proportionately: one canonical rule definition, cohesive components
  and hooks, explicit interfaces, low coupling, and no speculative abstractions.
- Preserve baseline evidence: Terminal production build passed before edits; the pre-existing
  test suite had 433 passing and one unrelated `colorTokens.test.ts` failure. The white knob
  is intentionally allowlisted; on Windows the computed `surfaces\routines.tsx` path does not
  match the POSIX `surfaces/routines.tsx` allowlist entry.
- Do not stage, commit, push, open a PR, edit another worktree, or touch unrelated runtime
  code. Do not add AI attribution.

---

### Task 1: Canonical React 19.2 engineering standard

**Files:**

- Create: `docs/docs/architecture/react-engineering-standard.mdx`

**Interfaces:**

- Consumes: P-book P1–P23, Delivery evidence language, the actual Terminal toolchain, and
  official React 19.2/Next 15/Testing Library documentation.
- Produces: stable rule IDs used by navigation links, checklists, the review matrix,
  rollout phases, and exception records.

- [ ] **Step 1: Create the baseline, scope, and severity contract**

Write MDX frontmatter, a repository-baseline table, applicability notes, and exact meanings
for MUST, SHOULD, and MAY. Mark each tool as `enforced now`, `available but not configured`,
or `proposed`.

- [ ] **Step 2: Define legacy-code classification and touched-code policy**

Define four non-overlapping classifications:

1. removed/incompatible React 19 API;
2. demonstrated correctness or performance defect;
3. architectural risk;
4. old-but-correct style.

Require migration only at the smallest safe boundary and prohibit unrelated mass cleanup.

- [ ] **Step 3: Write the complete rule catalog**

Create these stable rule IDs and cover each with the same eight fields:

| ID | Topic |
|---|---|
| `R-COMP-01` | pure, focused components and responsibility boundaries |
| `R-HOOK-01` | Rules of Hooks and focused custom Hooks |
| `R-STATE-01` | minimal state and derived values |
| `R-STATE-02` | state ownership, colocation, and lifting |
| `R-EFFECT-01` | Effects only for external synchronization |
| `R-EVENT-01` | event handlers, Effect Events, and dependency honesty |
| `R-DATA-01` | loading, caching, waterfalls, request races, and cancellation |
| `R-ACTION-01` | forms, Actions, `useActionState`, `useOptimistic`, transitions |
| `R-SUSPENSE-01` | Suspense plus explicit loading/error/empty states |
| `R-RSC-01` | Next Server/Client Component boundaries |
| `R-REF-01` | `ref` as a prop and compatibility use of `forwardRef` |
| `R-MEMO-01` | React Compiler readiness and evidence-based manual memoization |
| `R-STORE-01` | Context, external stores, selectors, `useSyncExternalStore` |
| `R-COMPOSE-01` | composition instead of mode-heavy universal components |
| `R-ERROR-01` | render, event, async, and route error handling |
| `R-A11Y-01` | semantic HTML, names, focus, keyboard, and live feedback |
| `R-TEST-01` | Testing Library tests of user-observable behavior |
| `R-API-01` | removed and deprecated React 19 APIs |
| `R-MIGRATE-01` | incremental migration and evidence-sized refactoring |

For every ID include:

1. level and applicability;
2. problem and real consequence;
3. executable-looking anti-pattern;
4. corrected example;
5. allowed exception;
6. concrete diff/review signal;
7. current or proposed automated check with an exact rule/tool/command;
8. false-positive risk and reviewer adjudication.

- [ ] **Step 4: Add the enforcement matrix and ready-to-use artifacts**

Add one matrix row for every rule ID:

`Rule → diff signal → automated check → manual check → allowed exception`.

Add:

- author pre-PR checklist;
- React reviewer checklist;
- blocking findings;
- non-blocking improvements;
- documented exception template with rule ID, scope, owner, reason, evidence, expiry,
  review date, and tracking link;
- audit order for existing code;
- staged ESLint/Compiler/test/CI rollout with entry criteria, checks, exit criteria,
  rollback, and false-positive controls.

- [ ] **Step 5: Add primary official references and self-review**

Use React, Next.js, TypeScript/ESLint where applicable, and Testing Library primary
documentation. Verify that no framework-dependent capability is described as mandatory
without the current Vexa support statement.

### Task 2: Discoverability without duplicated policy

**Files:**

- Modify: `docs/docs/docs.json`
- Modify: `docs/docs/architecture/README.md`
- Modify: `clients/terminal/README.md`
- Modify: `clients/terminal/docs/README.md`
- Modify: `docs/docs/llms.txt`

**Interfaces:**

- Consumes: canonical page slug `architecture/react-engineering-standard`.
- Produces: one public navigation entry and code-adjacent links; no second copy of rules.

- [ ] **Step 1: Add the page to Mintlify navigation**

Insert `architecture/react-engineering-standard` directly after
`architecture/modules` in the Architecture group. Preserve JSON formatting and all other
navigation entries.

- [ ] **Step 2: Update the Architecture index**

Add the React standard to the architecture directory inventory and identify it as scoped
to `clients/terminal`, subordinate to the P-book.

- [ ] **Step 3: Update Terminal front doors**

Link both Terminal README files to the canonical standard. Replace the stale “No test
suite yet” paragraph with the actual focused commands:

```powershell
pnpm --filter @vexa/terminal test
pnpm --filter @vexa/terminal build
```

State that build currently checks TypeScript while ESLint enforcement is not configured.

- [ ] **Step 4: Update the agent-facing docs index**

Add one “Engineering standards” link to `llms.txt` outside “Governance (the law)” so the
page is discoverable without redefining the governance trinity.

### Task 3: Review and verification

**Files:**

- Review all changed files from Tasks 1–2.

**Interfaces:**

- Consumes: complete uncommitted diff and baseline evidence.
- Produces: evidence-backed Expected → Actual → Verdict report with no claim above the
  altitude of the checks run.

- [ ] **Step 1: Run structural documentation checks**

Run:

```powershell
git diff --check
node -e "JSON.parse(require('fs').readFileSync('docs/docs/docs.json','utf8'))"
pnpm gate:readme
pnpm gate:docs-version
```

Expected: all pass. There is no repository-defined MDX or broken-link gate, so validate
internal paths and official URLs separately and report that limitation.

- [ ] **Step 2: Run focused content checks**

Use repository searches or a small read-only script to verify:

- every required rule ID exists in the catalog and matrix;
- every rule contains level, problem, anti-pattern, correction, exception, review signal,
  automation, and false-positive sections;
- MUST/SHOULD/MAY are defined;
- all requested checklists, rollout phases, audit order, and exception fields exist;
- React Compiler, Actions, and Server Components have repository applicability guards.

- [ ] **Step 3: Obtain independent read-only reviews**

Ask one reviewer to check requirement coverage and one reviewer to check official
React/Next/toolchain accuracy. Reviewers must not edit files.

- [ ] **Step 4: Run relevant product and repository gates**

Run:

```powershell
pnpm --filter @vexa/terminal test
pnpm --filter @vexa/terminal build
pnpm gate:node
node scripts/gates.mjs all
```

Expected: build and documentation-related gates should pass. The known color-token test
may remain red because this task does not modify its source. Do not rerun a deterministic
unchanged red merely to seek a flake. Do not claim the full gate green unless its raw output
is green.

- [ ] **Step 5: Final worktree and scope audit**

Confirm:

```powershell
git status --short --branch
git diff --stat
git diff --name-only
git diff -- clients/terminal/src
```

Expected: only the plan, canonical documentation, navigation, and README/index files are
changed; no runtime source, dependencies, lockfiles, staging, commits, or push.
