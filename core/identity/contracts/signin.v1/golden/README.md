# signin.v1 goldens

Reference instances that pin the contract. Filename = `<Shape>.<case>.json`; the part before the
first dot is the `$def` each must conform to. There is one `SigninAdmissionResponse.<reason>.json`
for every admission and refusal reason and one `AdminClaimResponse.<reason>.json` for every claim
reason; `../validate.mjs` fails when a reason has no golden.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
