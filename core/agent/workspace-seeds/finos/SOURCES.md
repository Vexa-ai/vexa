# Sources & data provenance

This knowledge graph (`kg/entities/`) was assembled **entirely from publicly available
FINOS sources** by the `finos-discovery` routine (see `routines/finos-discovery.md`).
Every entity file carries its own `Source:` line with the URL(s) it was derived from.

## Where the data comes from

All provenance traces to the **Fintech Open Source Foundation's own public material**:

| Source | What | Licensing |
|--------|------|-----------|
| `github.com/finos/*` | FINOS project repos, the Technical Oversight Committee repo (README + public candidacy issues), org member & contributor lists | Open source, predominantly Apache-2.0 |
| `api.github.com` | Public GitHub user/contributor profiles | GitHub public API |
| `finos.org`, `resources.finos.org`, `osr.finos.org` | FINOS staff bios, blog/podcast pages, OSR docs | FINOS published content |

The entity notes are **original short factual summaries** written by the agent (facts +
synthesis), not verbatim copies of source text. Facts are not copyrightable; the curated
prose is offered under CC BY 4.0 (see NOTICE).

## Privacy / personal data

`kg/entities/person/` contains professional, public-capacity information about identifiable
people active in FINOS (TOC members and candidates, FINOS staff, project maintainers/
contributors). It is limited to role/affiliation/contribution facts that the individuals or
FINOS published themselves (e.g. self-submitted TOC candidacy issues, FINOS people pages,
public GitHub profiles). No private contact details, no special-category data.

If you are listed here and want your entry corrected or removed, contact
**finostest@vexa.ai** and it will be updated or deleted promptly. We honor such requests
regardless of the public origin of the data.

> Note for redistributors: "publicly available" does not exempt personal data from privacy
> law (e.g. GDPR). If you fork and republish this graph, you take on the role of an
> independent data controller for the person entries — keep this notice and the removal path.
