# troubleshooting — failure references (Mintlify pages)

Companions to [`../troubleshooting.mdx`](../troubleshooting.mdx), which is the symptom-first page:
a reader arrives with something they observed and leaves with a fix. The pages in here are the
reference half — a reader arrives with a value the API gave them and leaves knowing what it means.

| Page | Answers |
|---|---|
| `completion-reasons.mdx` | why a terminal meeting ended — all ten `completion_reason` values, whose side each points at, and what to do |

`completion-reasons.mdx` enumerates the sealed `lifecycle.v1` `CompletionReason` enum
(`core/meetings/contracts/lifecycle.v1/lifecycle.schema.json`). **If that enum changes, this page
is the consumer that must change with it** — it claims "ten values, no more" in its own first
paragraph. Nav order lives in `../docs.json`.
