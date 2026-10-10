# outbound-url.v1 — may a server fetch a URL somebody else supplied

> **SEALED** — `outbound-url.schema.json` is pinned in `contracts.seal.json`; a change rides the
> human `lane:contract` review (`pnpm seal:contracts` re-pins the hash).

Several images fetch URLs a person or a model supplied: customer webhooks and calendar feeds
(meeting-api), the agent's web and asset fetches and the model endpoint (agent-api and the worker),
the transcription endpoint check (admin-api), custom-service calls and OAuth exchanges (the
credential broker), and the bot's transcription client. Each must refuse a URL that reaches this
deployment or its cloud: one rule, written here.

**The rule** (`ssrf.py` states it in full):
1. every address is checked as what it reaches — an IPv6 form that carries an IPv4 address
   (mapped, compatible, translated, 6to4, Teredo, NAT64, ISATAP) and a numeric host the resolver
   reads as IPv4 are read for that IPv4 address;
2. the refused ranges are explicit lists, not `ipaddress` flags;
3. the connection is pinned to the address that was checked, on every request and redirect;

and a host name that can only name this deployment (`localhost`, `*.localhost`, a cloud metadata
name, a single label) is refused before any lookup.

| File | What it is |
|---|---|
| `ssrf.py` | the canonical implementation (Python, stdlib only). Vendored verbatim into each Python image that fetches such a URL; `scripts/parity.json` fact `outbound-url-guard` fails the build when a copy differs. |
| `outbound-url.schema.json` | the shape of the case table (sealed). |
| `golden/outbound-url-vectors.json` | the case table: addresses, host names and URLs, each with its verdict. Both implementations' test suites read a byte-identical copy (fact `outbound-url-vectors`): meeting-api `tests/test_outbound_url_vectors.py` and `@vexa/transcribe-whisper` `src/url-guard.test.ts`. |
| `validate.mjs` | `gate:schema`: the golden and every copy a suite reads conform. |

**Changing the rule:** edit `ssrf.py` and the table here, copy both verbatim over every site the
two parity facts list, and keep both suites green. The seal covers the table's shape; the cases and
the code are held by the parity facts and the suites (ADR-0044).
