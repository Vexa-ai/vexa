# Security review protocol

Proves that nothing the candidate adds is reachable by an unauthenticated or cross-tenant path, that
every finding carries a demonstrated — not inferred — exploit or refutation, and that the dependency
state of the **shipped images** is known.

**Leg id:** `security-review` · **fires:** `both` (diff reading at formation; live probes at staging) ·
**re-run:** on every candidate re-cut and on any dependency-manifest change.

## Inputs

| Input | How to obtain |
|---|---|
| Diff range | `git diff --stat <prev-tag>..<head>` — the same range the blast-radius leg fixed |
| The **built** tree | `candidate-images.json` → `build_source`; then `git diff --stat <head> <build_source>` |
| Dependency manifests | `git ls-tree -r <head> -- 'pnpm-lock.yaml' '**/package-lock.json' '**/uv.lock' 'requirements*.txt'` |
| Which manifest each image installs | read every release `Dockerfile` — `npm ci` vs `pnpm`, `--omit=dev`, what is copied into the final stage |
| Open alerts | `gh api repos/Vexa-ai/vexa/dependabot/alerts?state=open --paginate` |
| Staged base URL | for read-only edge probes only |
| Throwaway credentials | from the operator's own secret store — verify they still authenticate before relying on them |

Never read source from the working tree. `git show <head>:<path>` for every claim: a checkout sitting on
an older branch returns pre-train content and produces the opposite finding.

## Input identity

The reviewed diff plus the dependency state of the shipped images — the shape
`releases/<version>/readiness.yaml` pins for this leg:

```
v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307 + deps@1f3c9ab27d40e6b8
```

```bash
DEPS=$(git ls-tree -r "$HEAD" -- pnpm-lock.yaml clients/terminal/package-lock.json \
        '*/uv.lock' '*/requirements*.txt' | shasum -a 256 | cut -c1-16)
printf '%s..%s + deps@%s\n' "$PREV_TAG" "$(git rev-parse "$HEAD")" "$DEPS"
```

A dependency bump changes the identity even when no source changes — that is deliberate.

## Method

1. **Settle tree identity first.** The reviewed commit is often not the commit the images were built
   from. Diff head against `build_source` and state the result in one line: in the rc.18 run four files
   differed — a workflow, a map test, the map, and the witness receipt — and **no source file differed**,
   so every finding applied exactly to the built candidate. If source differs, review the built tree.

2. **Read the diff for the trust boundaries, in this order:** identity-header stripping at the gateway ·
   `user_id` scoping in every new SQL predicate · whether a resource id is resolved *inside the caller's
   own document* or is a global key · mass assignment (`extra: forbid`, patch whitelists) · path
   parameters reaching a proxied upstream · new primitives added without a scope predicate.

3. **Prove it, do not infer it.** Every claim that a path is reachable or blocked is settled by
   execution: drive the **exact** client the shipped code constructs (the same transport builder, the
   same flags) against a local listener in a scratch directory, and paste the observed output —
   `BLOCKED` / `HTTP 200` / measured bytes. Reading a guard and pronouncing it sound is how the rc.18
   SSRF bypass survived a previous round: that review verified the *design*, which was good; the defect
   was one missing address family in a constant.

4. **Measure against the shipped code path at the shipped constants.** Run the actual parser, the actual
   cap, the actual timeout. Report numbers: bytes buffered before the cap fires, peak RSS, rows emitted
   per feed, CPU seconds per sweep. A cap that is checked after the whole body is in memory is a
   post-hoc report, and only a measurement says so.

5. **Write the reachability chain per finding**, every link named: entry route → auth requirement →
   rate limit and any cooldown → the primitive → **impact scope**. Impact scope is the field that
   decides severity: a single-tenant *path* into a shared control plane is cross-tenant *impact*.

6. **Dependencies: trace, do not trust the SBOM.** For each open critical/high alert, answer two
   questions separately: (a) does the vulnerable manifest get installed into a **published** image —
   Dockerfile by Dockerfile, lockfile by lockfile; (b) is there a first-party call path — the specific
   API, provider, or directive the advisory requires, grepped for in our code. Record "not reachable"
   **with the precondition that would make it reachable** (one `"use server"` file, one env flag turning
   on an outbound path). Collapse alert rows to distinct package × GHSA pairs before counting; Dependabot
   files one row per manifest.

   Two standing corrections found by the rc.18 run: the terminal **ships** (published image, and its
   `npm ci` tree is baked into lite), and `scripts/sbom.mjs` is a repo inventory that unions dev trees —
   it misreported a shipped version. A wrong SBOM is itself a finding: it is the artefact an auditor is
   handed.

7. **Secrets hygiene across every changed file:** keys, tokens, JWTs, private keys, URLs with embedded
   auth, default/fallback secrets, secrets written to logs. Then the same pass over **fixtures and eval
   corpora** — real participant names, real meeting links, real attendee emails entering a public repo
   is the higher-probability leak. Confirm with `git ls-tree` that the gitignored inputs were in fact
   never committed.

8. **Injection and XSS pass:** every new query parameterized, every f-string SQL identified and its
   interpolant proven to be a module constant; `dangerouslySetInnerHTML` / `eval` / `new Function` /
   `document.write` in shipped client code; escaping of any string that originates from a meeting
   participant.

9. **Write the "Checked, and sound" section.** Numbered, each item with `path:line` and what was
   *actively probed*, not assumed — identity boundary, cross-tenant expressibility, redaction on every
   read path, ownership checks before storage access, new surfaces that turned out inert. This negative
   space is half the deliverable: it is what lets the next reviewer skip what is settled and what makes
   a later regression visible.

10. **State what was not measured.** No image pulled, no layer scan, reachability inside third-party
    libraries verified for which packages only, production env values not read. An unmeasured area is a
    named gap, never silence.

11. **Probe log.** Every live probe, read-only, with what was created and what was torn down. Exploit
    demonstrations run **offline against the shipped source on localhost** — never against staging,
    never against a third party. If nothing was created, say "nothing to tear down".

12. **Assign a per-finding blocking verdict** using the rules below, and put the verdict table at the top
    of the findings document.

## Receipt

`releases/v0.12.23/readiness/security-review.receipt.json`:

```json
{
  "schema_version": 1,
  "leg": "security-review",
  "candidate_map_sha": "3422d02f02985ab0f02fc47f58a6b4b3e23f0163397f1073c004fe44624d764f",
  "input_identity": "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307 + deps@1f3c9ab27d40e6b8",
  "result": "green",
  "findings_ref": "https://github.com/Vexa-ai/vexa/issues/1179",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session · security-review)"
}
```

```bash
V=v0.12.23; mkdir -p releases/$V/readiness
MAP_SHA=$(shasum -a 256 releases/$V/candidate-images.json | cut -d' ' -f1)
jq -n --arg s "$MAP_SHA" --arg i "$IDENTITY" --arg r green --arg f "$FINDINGS_URL" \
  '{schema_version:1,leg:"security-review",candidate_map_sha:$s,input_identity:$i,result:$r,
    findings_ref:$f,generated_at:(now|todate),generated_by:"agent (readiness session)"}' \
  > releases/$V/readiness/security-review.receipt.json
node release/readiness/check.mjs --phase staging --release $V
```

A re-cut changes the map sha and the receipt goes `stale` — re-run the leg; the diff range may be
unchanged but the shipped bytes are not.

## Blocking-verdict discipline

Every finding carries its own `Blocks?` cell in the findings table. The leg's `result` is `red` if any
cell says yes.

**RED — blocks the train:**
- Reachable **unauthenticated**, at any severity.
- Reachable **cross-tenant** — one account reading, writing or deleting another's data.
- HIGH proven reachable by any authenticated account with **cross-tenant impact**, including availability
  attacks on a shared control plane. This clause exists because the rc.18 run found the plain
  cross-tenant-*path* rule missing exactly this class: one authenticated user OOMs `meeting-api` and
  every tenant goes down with it.
- A destructive primitive newly added to a channel whose writers are hostile-input-facing, with no
  scope check.
- Any HIGH left **unmeasured** — "probably not reachable" is not a verdict, and the leg cannot be green
  while one exists.
- A dependency vulnerability with a demonstrated first-party reachable path in a published image.

**Rides to the next train, with a filed issue and a named owner:** authenticated, single-tenant HIGH
where the fix is not trivial; MEDIUM / LOW / INFO; dependency alerts with no demonstrated path — carry
the precondition sentence into the issue so the next review starts from it.

**Fix in-train regardless of the verdict** when the patch is a few lines and closes a pre-existing
exposure at the same time. Cheapness is a reason to fix, never a reason to downgrade.

**Waivers:** founder only, per finding, recorded in the receipt's `waivers[]` with the finding id, the
issue link, and the release it expires in. A waiver on an unmeasured finding is not available — measure
first.

## Failure modes

- **Reviewing the design instead of the constant.** A guard whose structure is right and whose list is
  missing one address family passes every design review and fails in production.
- **Inferring reachability from a grep.** Greps establish *absence of a call site today*; they establish
  nothing about a transport, a parser, or a redirect. Execute the shipped path.
- **Trusting the repo SBOM as an image inventory.** It unions dev trees and non-shipping harnesses, and
  it has misreported a shipped version.
- **Assuming a client is dev-only.** The terminal is published and is baked into lite; every alert on its
  tree rides into production images.
- **Reviewing the wrong tree** — a stale working tree, or the reviewed head when the images were built
  from a different commit.
- **Closing a finding on a trust boundary that holds today.** "Redis is internal" was true and still left
  a cross-meeting delete primitive on a channel written by containers that parse hostile meeting pages.
- **A receipt bound to a stale candidate map, or to a pre-bump dependency identity** — the shipped bytes
  changed and the review did not.
