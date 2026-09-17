# Contributing to Vexa

The whole delivery loop — roadmap, claiming, the acceptance-table merge promise, validation —
is one page: **https://docs.vexa.ai/governance/delivery** (source:
[`docs/docs/governance/delivery.mdx`](docs/docs/governance/delivery.mdx)).

**Opening a PR? Come say hi on [Discord](https://discord.gg/Ga9duGkVz9) (highly recommended, not
required).** A sentence or two on **what** your change does and **why** helps us review your value
bundle faster and plugs you into the reporter and maintainers. Your PR is judged on its evidence,
never on whether you show up — but showing up makes everything smoother.

Working with an agent, or in a checkout? Start at [`AGENTS.md`](AGENTS.md) — intake, the
one-call roadmap fetch, claiming, and the session rules. Security reports: [`SECURITY.md`](SECURITY.md).

## Community triage and peer review

**Every Wednesday, 14:00 UTC, 45 minutes** — open to anyone, the same link every week, no invitation
needed: **join** `<MEET_URL>` · **calendar** [`meetings/community.ics`](meetings/community.ics) ·
**minutes** [Discussions](https://github.com/Vexa-ai/vexa/discussions).

We walk the open pull requests and issues **oldest first**, and each one leaves with an owner and a
label, or it is closed. Anyone blocked on *us* comes before the queue. You do not need to have
contributed anything to come, and you do not have to attend for your pull request to be walked.

**A change merges when a human who did not write it has run it and said what happened.** Not "looks
good", and not "CI is green" — the machine already said that. Someone ran it, on their own instance.
[`VALIDATION.md`](VALIDATION.md) is the playbook: how to bring a pull request up in one command,
what to exercise, and the exact shape of the comment to post. **One validation makes you a
validator**, which is the lowest rung of the ladder and the only one you can reach without writing
any code.

**The trade, plainly: your pull request gets its human test when you have tested someone else's.**
Pick one from the board, run it, post what happened. It is a norm rather than a check — nobody's
first contribution is held hostage to it — and it is the fastest way to become a reviewer here.

Contributors who review regularly are given GitHub **triage** permission: labels, assignees,
milestones, closing duplicates. Merge and release stay with the maintainers. Roles, how you move
between them, and the full merge rule: [`GOVERNANCE.md`](GOVERNANCE.md). Who merges what:
[`MAINTAINERS.md`](MAINTAINERS.md). The call is transcribed by a Vexa bot — the project uses its own
product — and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) applies.

## Rights and DCO — one choice, then automatic

Every new pull request selects exactly one rights path: independent, employer/client-controlled,
or unsure. Independent contributors sign each commit under DCO 1.1 and do **not** sign an
individual CLA. Corporate or uncertain contributions enter private rights review while technical
review continues; only merge waits. Read [`CONTRIBUTOR_RIGHTS.md`](CONTRIBUTOR_RIGHTS.md) for the
exact choices, `git commit --signoff` workflow, safe remediation commands, and corporate process.
