# Contributor rights

Vexa accepts contributions under Apache-2.0 while preserving a reviewable record that the person
or organization supplying each contribution had the right to do so. The normal individual path is
one declaration in the pull request plus the standard Developer Certificate of Origin (DCO) on
each commit. An individual CLA is not required on that path.

This policy applies to pull requests opened after the activation pull request recorded in
`.github/contribution-rights.json`. Earlier merged work is not rewritten or retroactively declared
non-compliant. Concrete ownership concerns in earlier work are reviewed individually.

## Choose one path when opening a pull request

### Independent

Choose **Independent** when you created the contribution, or otherwise have the right to submit
it under Apache-2.0, and no employer, client, or other organization owns or controls it. Employment
or a corporate email address alone does not force the corporate path.

Sign every commit under [DCO 1.1](https://developercertificate.org/) by using:

```bash
git commit --signoff
```

The sign-off is a legal certification, not a cryptographic signature. It must use the name and
email of the commit author. Vexa's required DCO App check validates every commit separately. A
second required `dco-no-override` check rejects the app's write-user override and any unknown
success result.

### Employer/client authorization required

Choose this path when the contribution is assigned work, was prepared using controlled employer
or client assets, is sponsored for upstream submission, or may otherwise be owned or controlled
by another legal entity. Continue to sign your own commits under the DCO. Technical review may
continue while authorization is arranged, but merge waits.

The normal route is self-serve: forward
[`CLA/employer-authorization-template.md`](CLA/employer-authorization-template.md) to whoever
handles open-source approvals where you work. It is a short letter they fill in, sign, and email
to [dmitry@vexa.ai](mailto:dmitry@vexa.ai) — it adds no terms beyond what Apache-2.0 already
provides. You sign nothing beyond your own DCO sign-off.

If your organization's process cannot route a letter it has not seen before, Vexa also accepts the
[ASWF 2020 v2 CCLA](CLA/ccla-aswf2020.md) — the Academy Software Foundation's Apache-derived
corporate CLA, reproduced with only its header fields completed. It is an accepted alternative and
never a requirement; either instrument satisfies this path. Note that it is per-project, so an
ASWF CLA signed for another project does not cover Vexa.

If you cannot reach your own approvers, or they would rather hear from Vexa directly, email
[dmitry@vexa.ai](mailto:dmitry@vexa.ai) with the pull-request URL and the rights holder's legal/IP
contact instead. Do not attach executed agreements, signatures, addresses, employment documents,
or private correspondence to a public issue or pull request.

### Unsure

Choose **Unsure** as early as possible. Technical review may continue. Vexa will help determine
whether the independent or corporate path applies, without asking you to draft legal language.

## What the automated gate proves

The `contribution-rights` check requires exactly one declaration. The corporate path passes only
after a designated verifier records an opaque private-register receipt against the exact pull
request number and current head SHA. A later push invalidates the verification. A verifier can
also place an independent declaration into rights review when concrete facts contradict it.

The executed agreement and personal information remain in the private register. Public checks show
only an opaque receipt identifier and verification state. A corporate authorization is evidence of
contribution rights; it is not evidence of a customer, deployment, partnership, endorsement,
revenue, procurement, or permission to use the organization's name or marks.

## Fixing a DCO failure

For the latest commit:

```bash
git commit --amend --no-edit --signoff
git push --force-with-lease
```

For several commits on a branch only you use:

```bash
git rebase --signoff <merge-base>
git push --force-with-lease
```

If other people use the branch, do not rewrite it without their agreement. The DCO App's individual
remediation-commit flow is enabled so the original author can add a retrospective certification
without a third party signing for them. Vexa does not enable third-party remediation and a
maintainer will never manufacture another person's sign-off.

## Agent-assisted contributions

An agent may explain this policy, inspect commits, use `--signoff` after the human has explicitly
chosen a rights path, and prepare a repair. It must not choose the legal path, certify rights,
change global Git identity, add another person's sign-off, or rewrite/push history without the
human's explicit approval. The desired experience is one conscious legal choice and no Git
ceremony.

### Making sign-off automatic (agent runbook)

Once — and only once — the human has chosen a rights path, an agent should set the working copy up
so the human never handles DCO mechanics again.

Git has no `commit.signoff` setting. `format.signOff` exists but affects `format-patch` only, and
Git's own documentation notes that adding the trailer "should be a conscious act." That caution is
satisfied by the human's explicit choice of rights path; what follows just carries it forward
consistently. Install a repository-local hook:

```bash
cat > .git/hooks/prepare-commit-msg <<'HOOK'
#!/bin/sh
name=$(git config user.name)
email=$(git config user.email)
[ -n "$name" ] && [ -n "$email" ] || exit 0
grep -qsF "Signed-off-by: $name <$email>" "$1" && exit 0
printf '\nSigned-off-by: %s <%s>\n' "$name" "$email" >> "$1"
HOOK
chmod +x .git/hooks/prepare-commit-msg
```

It is repository-local (`.git/hooks` is never committed, so it reaches no one else), uses the
contributor's own configured identity rather than an agent-supplied one, is idempotent, and exits
quietly if the identity is unset rather than writing a malformed trailer.

Verify before relying on it, since a silent hook failure looks identical to success:

```bash
git commit --allow-empty -m "dco hook check" && git log -1 --format=%B | grep "Signed-off-by:"
```

Then drop the check commit with `git reset --hard HEAD~1`.

Repairing commits that predate the hook is covered under *Fixing a DCO failure* above. An agent may
run those commands on a branch only the human uses; on a shared branch it must ask first, because
the repair rewrites history.

**The line an agent does not cross:** it may install and verify the mechanism, and it may repair its
own or the human's unsigned commits after the rights path is chosen. It may not choose that path,
and it may not sign for anyone else — including adding a `Signed-off-by` naming a person who has
not made that certification themselves.

## Previously merged contributions

Missing DCO evidence does not by itself prove that an Apache-2.0 contribution is unlicensed.
Historical review is risk-based:

- small contributions with no ownership signal are recorded without outreach;
- significant or ambiguous individual contributions may receive a separate retrospective DCO
  attestation from the original contributor;
- employer-owned or corporate-directed work requires a corporate authorization covering named
  pull requests or commit SHAs; and
- material work that cannot be cleared is replaced or removed.

Vexa never rewrites history merely to insert a sign-off and never signs for a contributor.

## Administration

The designated verifier compares the proposed public decision with the private register before
posting it. A verified record must identify the legal entity, authorized signer, contributor and
GitHub identity, covered repository and contribution, agreement version and SHA-256, effective
dates, limitations, private document location, and the current PR head SHA.

Public verifier decision format:

```text
<!-- vexa-contribution-rights-decision:v1 -->
Decision: verified
Receipt: VCR-YYYY-NNNN
PR: #NNN
Head: <40-character commit SHA>
```

`Decision: review` opens a persistent hold. `Decision: cleared` closes that hold for an independent
contribution at the named head. Only identities listed in `.github/contribution-rights.json` are
accepted, and all decisions are re-evaluated in the merge queue.

Before activating the gate, repository administrators must:

1. install the maintained [DCO App](https://github.com/apps/dco) and require both `DCO` and
   `dco-no-override` checks;
2. enable GitHub's compulsory sign-off for web-based commits;
3. replace `__BOOTSTRAP_PR__` with this policy PR's number;
4. require the `contribution-rights` check, with the ruleset bypass list set to none; and
5. verify the private register, retention rule, and return channel.
