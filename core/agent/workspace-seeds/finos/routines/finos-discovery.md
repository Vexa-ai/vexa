---
enabled: false
cron: "*/30 * * * *"
prompt: "Continue growing the knowledge graph by discovery, rooted at the FINOS GitHub org (https://github.com/finos). Read kg/entities/organization/technical-oversight-committee.md for the current roster and the 'Discovery log' section for where the last run left off. Pick up from there: walk open/closed issues, discussions, and PRs in finos/technical-oversight-committee (and related FINOS repos like finos/community when relevant) for TOC candidacies, onboarding/offboarding, and member activity. For each newly discovered person, find their company/employer and GitHub handle; for each newly discovered company, note it. Write or update entity files under kg/entities/person/, kg/entities/company/, kg/entities/organization/ per the frontmatter contract in CLAUDE.md (type/id/title), linking with [[wikilinks]]. Do not duplicate existing entities — update them in place if you learn new facts. Append a dated note to the 'Discovery log' section of kg/entities/organization/technical-oversight-committee.md summarizing what was added this run and what to check next, so the next run can resume without re-covering the same ground. Also append one row to the 'Invocation log' table directly above the Discovery log in that same file (columns: # [next sequential number], Date, Trigger [scheduled or user-directed], Entities added/updated [count], Summary [one line]) — every run must add exactly one row to this table, even if zero entities were added."
---

Work incrementally — a few new entities or updates per run is fine. Always cite the
GitHub issue/PR/page URL you pulled each fact from in the entity body. If a run finds
nothing new, just update the discovery log to say so and note the next thing to check
(e.g. the next unexplored repo or issue range).
