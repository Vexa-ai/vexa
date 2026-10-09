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

## Rights and DCO — one choice, then automatic

Every new pull request selects exactly one rights path: independent, employer/client-controlled,
or unsure. Independent contributors sign each commit under DCO 1.1 and do **not** sign an
individual CLA. Corporate or uncertain contributions enter private rights review while technical
review continues; only merge waits. Read [`CONTRIBUTOR_RIGHTS.md`](CONTRIBUTOR_RIGHTS.md) for the
exact choices, `git commit --signoff` workflow, safe remediation commands, and corporate process.

## Where work happens

Vexum is where contributions land. Open your pull request on Vexum, get it
reviewed on Vexum, and it is merged and tagged on Vexum. Releases are cut
there.

The Vexum repository and `Vexa-ai/vexa` are kept in sync automatically in both
directions. You do not need to mirror anything by hand, open a second pull
request, or track which side a commit came from. If the two ever diverge, the
sync stops and a maintainer resolves it — your branch is unaffected.
