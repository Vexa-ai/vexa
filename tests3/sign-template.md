# Sign template — canonical

Defines the canonical sign block every human-gate artefact uses. Single source of truth.

This template separates two things that were conflated before:

- **What the human authors** — the entire release doc (every section, every line of prose). AI drafts are scaffolding; the human reviews, edits, and rewrites in their own voice before sign. The sign attests authorship.
- **What the human signs** — a small structured tail at the end of the doc carrying identity, timestamp, attestation flags, and a pointer back to what was signed. Always YAML; always the shape below.

A signed doc that wasn't human-authored is a finding at the next-stage audit, even if the YAML sign block is technically valid.

---

## What a sign means (four attestations)

When the human fills the sign block, they are personally attesting all four:

1. **I have read this multiple times.**
2. **I confirm it is true to the best of my knowledge and effort, and I finally understand what we are doing here and why.**
3. **I confirm this is the balance I can deliver in this release — the right trade-off between scope, time, risk, debt, and capacity given where we actually stand.**
4. **I authored this document.** AI may have drafted sections as scaffolding, but every line of prose has passed through me. The voice is mine. If something here is wrong, the error is mine, not the assistant's.

A sign is durable. Once signed, the doc is your word. If later evidence contradicts it, write a new doc (revision, addendum, retraction) — never edit the signed one in place.

A sign is bounded by knowledge and effort. It is not a guarantee of correctness; it is an honest statement of "this is what I understood after reading carefully, in my own voice."

---

## What AI may do, and may not do

**May:**
- Research the codebase, query prod, gather evidence.
- Draft scaffolding sections (proposed structure, table skeletons, candidate phrasings).
- Point out gaps, omissions, or inconsistencies between sections.
- Validate that the sign block is well-formed.

**May not:**
- Sign on the human's behalf. Flipping `signer`, `signed_at`, or any `attestation_confirmed: true` is a human-only action, full stop.
- Author the final prose. If the doc that reaches sign-time reads like AI prose, the human has not authored it — return it to authoring.
- Urge signing. Phrases like "ready to sign?", "looks good — sign it", "all clear" are forbidden. The human signs on their own timing.

---

## One doc, four signs — the control points

The release doc (`scope.md` → `RELEASE_NOTES.md`) travels the entire
release cycle. The human signs it **four times**, once at each
level-boundary exit:

| # | Sign at | When | What it attests |
|---|---|---|---|
| 1 | `plan-human` (exit plan level) | After plan-audit findings reviewed | "This is the plan I commit to deliver. The doc above is in my voice." |
| 2 | `develop-human` (exit dev level) | After local LOCAL=1 validation green + local-human-checklist walked | "We built what we planned. The doc still describes what we shipped to LOCAL=1. Local validation is green; checklist eyeballed." |
| 3 | `stage-human` (exit stage level) | After canonical validate matrix green + code review + canonical-stack eyeroll | "Canonical validation is green. The doc accurately describes what was verified on production-equivalent infra." |
| 4 | `release` (exit release level) | After merge + tag + `:latest` promotion | "The release shipped per the doc. `main`, image tags, and release notes match." |

The same physical doc carries all four signs. New revisions are
allowed BETWEEN signs (if scope shifts during develop or stage); each
new sign attests against the current revision via `signed_artefact.revision`
+ `git_sha`. Older signs stay in the audit trail and remain valid for
their revisions.

If a sign at any stage fails its attestation, the doc returns to the
authoring loop — the release does not exit the current level.

## Canonical block — copy this verbatim at the end of every signed artefact

The artefact carries a `signs:` list. Each entry is one stage's sign,
identical in shape but with the stage-specific attestations.

```yaml
signs:

  # ───────────────────────────────────────────────────────────────────
  # Sign 1 — at plan-human (exit plan level)
  # Attests: this is the plan I commit to deliver; doc is in my voice.
  # ───────────────────────────────────────────────────────────────────
  - stage: plan-human
    signer: ""                          # human only — not AI
    signed_at: ""                       # ISO-8601 UTC at sign time
    attestation_confirmed:
      read_multiple_times: false
      understands_what_and_why: false
      confirms_balance_deliverable: false
      i_authored_this_doc: false        # whole doc above, in my voice
    signed_artefact:
      path: ""                          # this file
      revision: ""                      # e.g. "v3 (2026-05-12 mid-cycle scope cut)"
      git_sha: ""
    rationale_in_my_own_words:          # signer authors; AI cannot pre-fill
      what_we_are_doing: |
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |

  # ───────────────────────────────────────────────────────────────────
  # Sign 2 — at develop-human (exit dev level)
  # Attests: we built what we planned; LOCAL=1 validation green; local
  # human-checklist walked; doc still describes what we shipped locally.
  # ───────────────────────────────────────────────────────────────────
  - stage: develop-human
    signer: ""
    signed_at: ""
    attestation_confirmed:
      doc_still_describes_reality: false
      local_validation_green: false       # LOCAL=1 lite + compose
      local_human_checklist_walked: false
      i_authored_any_new_prose: false     # since last sign
    signed_artefact:
      path: ""
      revision: ""                        # may be same as sign 1 if doc unchanged
      git_sha: ""
    notes_since_prior_sign: |             # optional; required if revision changed
      What changed between plan-human and develop-human, if anything.

  # ───────────────────────────────────────────────────────────────────
  # Sign 3 — at stage-human (exit stage level)
  # Attests: canonical validate matrix green; doc describes what was
  # verified on production-equivalent infra; code review + eyeroll done.
  # ───────────────────────────────────────────────────────────────────
  - stage: stage-human
    signer: ""
    signed_at: ""
    attestation_confirmed:
      canonical_validate_matrix_green: false   # all required modes
      code_review_approved: false
      canonical_stack_eyeroll_approved: false
      doc_still_describes_reality: false
      i_authored_any_new_prose: false
    signed_artefact:
      path: ""
      revision: ""
      git_sha: ""
    notes_since_prior_sign: |

  # ───────────────────────────────────────────────────────────────────
  # Sign 4 — at release (exit release level)
  # Attests: the release shipped per the doc; main, image tags, and
  # release notes match what was signed at stage-human.
  # ───────────────────────────────────────────────────────────────────
  - stage: release
    signer: ""
    signed_at: ""
    attestation_confirmed:
      merged_to_main: false
      git_tag_pushed: false              # e.g. v0.10.6.1
      images_promoted_to_latest: false
      env_example_fixed_on_main: false
      release_notes_match_what_shipped: false
    signed_artefact:
      path: ""                           # post-rename: RELEASE_NOTES.md
      revision: ""
      git_sha: ""
    notes_since_prior_sign: |
```

### `rationale_in_my_own_words:` — the compression test

Each sign entry carries a `rationale_in_my_own_words:` block. Three fields:

```yaml
    rationale_in_my_own_words:
      what_we_are_doing: |                                    # the signer's summary
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |
```

**Why this field exists alongside the authored body.** The doc body is the full statement; the rationale is the signer's *compression* of it. Two distinct functions:

1. **Compression test.** Can the signer summarise the whole doc in a few lines? If they can't, they don't understand it. The doc isn't ready to sign.
2. **Alignment check.** Does the compressed version match what the doc body actually says? If the signer writes "we ship 14 items" and the doc lists 20, one of them is wrong. The audit at the next stage cross-checks; divergence is a finding.

**Copy-paste is allowed if it's deliberate.** The signer may copy the doc's own headers into the rationale fields if those headers genuinely compress the doc. The forbidden case is auto-paste: clicking a "fill from headers" affordance, never reading. The rule isn't *don't reuse the doc's words*; it's *write the rationale yourself, with intent*.

**AI cannot write the rationale. AI's only role here is the alignment check.** No drafting, no structure suggestions, no candidate phrasings, no "you could say X." The signer writes from scratch. If the signer asks AI for help phrasing their rationale, AI declines with: *"Rationale must originate with you. I'll check alignment after you write."* The only way AI participates is the audit pass that compares the human-authored rationale against the doc body and flags drift.

**Audit alignment check (at `plan-audit`, `develop-audit`, `stage-audit`):**

- Run an LLM-or-human pass that compares each `rationale_in_my_own_words` block to the doc body at the same revision.
- Flag findings where the rationale claims something the doc doesn't say, or omits something the doc emphasises as HIGH significance.
- A drift between rationale and doc body is a BLOCKER finding — either the rationale doesn't reflect what's being shipped, or the doc has shifted under the rationale. Bounce to authoring.

The compression test catches signers who haven't internalised the doc. The alignment check catches signers (and audits) who have let the rationale and the doc drift apart over the cycle.

---

## Validation rules (enforced by `stage.py` at each level-boundary exit)

`stage.py` validates the **single corresponding sign** at each
level-boundary transition — not the whole `signs:` list. Rules common
to every sign:

1. The sign for the current stage is present in `signs[]`.
2. `signer` is a non-empty `<local>@<domain>`.
3. `signer` is NOT an AI identifier (`claude@*`, `ai@*`, `assistant@*`).
4. `signed_at` parses as ISO-8601 UTC.
5. `signed_at` does not predate the artefact's last-modified time on disk.
6. Every `attestation_confirmed.*` flag for that stage is `true`.
7. `signed_artefact.path` points to the doc carrying this sign.
8. `signed_artefact.git_sha` matches the current `HEAD` of the file (proves the sign is for the present revision, not a past one).
9. Earlier signs (for prior stages) are intact and were valid for their revisions; if the doc has been revised since an earlier sign, `notes_since_prior_sign:` is non-empty on the next sign.
10. `rationale_in_my_own_words` has all three fields (`what_we_are_doing`, `why_right_call_given_current_state`, `what_im_uncertain_about_and_what_would_change_my_mind`) non-empty.
11. The rationale alignment check passes — rationale claims do not contradict the doc body at the same revision, and the doc's HIGH-significance items are not omitted from the rationale.

If any rule fails, AI MUST refuse to transition forward AND MUST NOT
prompt the human to "just fill it in to advance." Surface what is
missing and wait.

### Per-stage attestation flags

| Stage | Required `attestation_confirmed:` flags |
|---|---|
| `plan-human` | `read_multiple_times`, `understands_what_and_why`, `confirms_balance_deliverable`, `i_authored_this_doc` |
| `develop-human` | `doc_still_describes_reality`, `local_validation_green`, `local_human_checklist_walked`, `i_authored_any_new_prose` |
| `stage-human` | `canonical_validate_matrix_green`, `code_review_approved`, `canonical_stack_eyeroll_approved`, `doc_still_describes_reality`, `i_authored_any_new_prose` |
| `release` | `merged_to_main`, `git_tag_pushed`, `images_promoted_to_latest`, `env_example_fixed_on_main`, `release_notes_match_what_shipped` |

---

## Anti-patterns (rejected at the next-stage audit)

- **`signer` matching AI identifier.** AI cannot sign. Reject.
- **`signed_at` predating the doc's last-modified.** Sign claims to precede the artefact; either timestamp is wrong, sign is for a prior revision, or the doc was edited after signing (which is forbidden).
- **Three or four attestation flags flipped together in one commit.** Suggests rubber-stamping. Healthy signs flip the flags individually as the human walks through the doc; the audit trail (git history of the sign block) shows real reading time.
- **Identical doc content across multiple signed artefacts in the same release.** Suggests copy-paste signing rather than per-artefact authorship.
- **An empty doc with only the sign block.** No body to author = nothing to sign.

---

## Where this template is used (the human gates)

| Stage | Artefact carrying the sign block |
|---|---|
| `plan-human` | `releases/<id>/scope.md` (the release doc; author + sign together) |
| `develop-human` | `releases/<id>/local-human-checklist.yaml` (sign block at bottom; checklist items above are the authored content) |
| `stage-human` | `releases/<id>/human-approval.yaml` (alongside `code-review.md` + `human-checklist.yaml` — sign attests authorship of all three) |
| `release` | `RELEASE_NOTES.md` — same shape; the release notes ARE the authored doc |

---

## How the human and AI actually work together

In practice the workflow looks like:

1. AI proposes a structure + drafts each section.
2. Human reads each draft, edits or rewrites in their voice, possibly bouncing back to AI for research / clarification.
3. Iterate until every section reads as the human's own work.
4. Human fills the sign block at the end (after the four attestations are honestly true).
5. The signed doc is durable.

If at step 2 the human signs without rewriting, they're rubber-stamping AI's draft — that's the anti-pattern. The audit at the next stage catches it by reading the prose voice.

---

## Security findings and the public sign

The release doc signed at each control point is a **public artefact**:
it lives in the OSS repo, becomes `RELEASE_NOTES.md` at release, and
is read by customers, contributors, and competitors.

Security findings — fixed or unfixed — **MUST NOT appear in the
signed release doc**, even in the per-issue "What you get" rows, even
implicitly. The release doc carries only the **boolean** gate status
for security:

- ✅ passed → no HIGH/CRITICAL security findings are unmitigated and
  unreleased; every finding is tracked in the private channel below.
- ⏳ pending → the audit has not run yet at this stage.
- ❌ failed → there is a HIGH/CRITICAL finding without mitigation or
  private-disclosure timeline. The release does not exit the current
  stage.

### Where security findings actually live

| Finding type | Public release doc | Private channel |
|---|---|---|
| Pre-existing weakness we discovered this cycle but did not fix | ❌ never mention | ✅ GitHub Security private advisory; disclosure per project policy |
| Vulnerability we fixed this release | ❌ no CVE-style detail; no enumeration of what was broken | ✅ GitHub Security advisory published per disclosure policy (may be embargoed) |
| Vague hardening (e.g., "tightened auth" — no specifics) | ⚠ allowed only if it cannot be reverse-engineered into a CVE-class hint | ✅ Full detail in private advisory |
| Architectural decisions with security implications | ✅ allowed in ADRs (e.g., "no alembic — friction is the feature") if framed as design rationale, NOT as "we found this attack vector" | ✅ Threat model in private advisory if applicable |

### Per-cycle workflow

1. `plan-audit` and `stage-audit` apply principle 6 (security) per
   `tests3/audit-categories.md`.
2. Findings — fixed or unfixed — get filed as **GitHub Security
   private advisories** (`gh security advisory create` or via the
   web UI). Never in `scope.md`, `RELEASE_NOTES.md`,
   `plan-audit-findings.md`, or any other public artefact.
3. Each finding gets a disclosure timeline: when the advisory
   becomes public, whether a CVE is requested, what the embargo
   window is.
4. The public release doc carries only the gate status
   (✅ / ⏳ / ❌). The boolean is true when every finding has either
   a fix in this release OR a private advisory with a disclosure
   plan.

### What AI must do

- When asked to author the security row in `scope.md` or
  `RELEASE_NOTES.md`, emit ONLY the boolean status + a pointer to
  the canonical advisory channel.
- Never enumerate findings — fixed or unfixed — in the public doc.
- If the human asks AI to add a security detail to the public doc,
  refuse with: "Security details belong in a private GitHub Security
  advisory, not the public release doc — see `tests3/sign-template.md`
  Security findings section."
- If AI is researching code and finds a vulnerability, surface it to
  the human privately (in this conversation) and recommend the human
  file a GitHub Security advisory. Do not write it into the public doc.

### Audit at next stage

The audit at `plan-audit` and `stage-audit` checks that:
- Public docs carry no CVE-class hints.
- Every security finding has a corresponding private advisory.
- The disclosure-timeline column is filled per advisory.

A public doc with a CVE-shaped sentence is a BLOCKER finding —
release does not exit the stage until the sentence is removed and
moved to the private channel.

## Updating this template

Same rules as before. Write the new version in a `proposed:` block below, open a v0.10.7+ groom-pack `sign-template-vN`, mirror once approved. Existing signed docs validate against the version in force at their sign time.
