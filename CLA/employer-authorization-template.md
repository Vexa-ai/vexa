# Employer authorization letter

Use this when your employer, client, or another organization may own or control the work you are
contributing.

**Forward the letter below to whoever handles open-source approvals where you work** — an OSPO if
your organization has one, otherwise legal or your manager. They fill in five fields, sign, and
email it to <dmitry@vexa.ai>. You sign nothing beyond the standard DCO sign-off on your own
commits (`git commit --signoff`).

Technical review of your pull request continues while this is in flight. Only the merge waits.

There is nothing in the letter to negotiate. Section 5 of the Apache License 2.0 — this project's
license — already places contributions under the project's own terms unless the submitter states
otherwise. The letter confirms that the submitter is authorized, and adds no Vexa terms of its own.

---

## The letter

> **To:** Vexa.ai Inc. — dmitry@vexa.ai
> **Re:** Authorization to contribute to Vexa (Apache-2.0)
>
> **1. Organization:** `{full legal entity name}`, `{address}`
>
> **2. Signer:** `{name}`, `{title}` — authorized to give this confirmation on the
> organization's behalf.
>
> **3. Contributor:** `{name}`, GitHub `@{handle}`
>
> **4. Scope** — *select one:*
>
> - ☐ **Ongoing** *(simpler for both sides)*: all contributions by the above contributor to
>   `{repo}`, until we notify Vexa otherwise.
> - ☐ **This contribution only:** repository `{repo}`, pull request `{PR URL}`, at commit
>   `{head SHA}`.
>
> **5. Confirmation.** The organization is aware that the contributor is submitting the work
> above to Vexa's Apache-2.0 licensed project, authorizes that submission, and does not state
> otherwise within the meaning of Section 5 of the Apache License 2.0. The contribution is
> therefore submitted under the Apache License 2.0, including its Section 2 copyright grant and
> Section 3 patent grant. To the extent any grant must come from the organization rather than
> arising under Section 5, the organization grants it on those same terms and no others.
>
> Withdrawing an ongoing authorization applies to future contributions only; licenses already
> granted under Sections 2 and 3 are irrevocable by their own terms.
>
> **Signed:** `______________________`  **Date:** `______________`

---

## After it is sent

The signed letter is held privately and is never committed to this repository. Your pull request
receives only an opaque `VCR-YYYY-NNNN` receipt, bound to the pull request and its exact head
commit — no organization name, signer, or document content appears publicly. A later push
re-opens verification against the new head.

See [`CONTRIBUTOR_RIGHTS.md`](../CONTRIBUTOR_RIGHTS.md) for the full policy and the public
verifier decision format.

## If this route does not fit

| Situation | What to do |
|---|---|
| You created the work and no organization owns or controls it | Nothing beyond the DCO sign-off — choose **Independent** in the pull-request template |
| You cannot reach your own approvers, or they would rather hear from Vexa directly | Email <dmitry@vexa.ai> with the pull-request URL and your legal/IP contact |
| Your legal team requires a signed contributor agreement on file regardless | See [`README.md`](README.md) — note that no version is adopted yet |
| You are not sure which applies | Choose **Unsure** in the pull-request template as early as possible; review continues while we work it out |
