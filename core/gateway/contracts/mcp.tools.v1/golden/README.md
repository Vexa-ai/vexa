# mcp.tools.v1 goldens

`Manifest.<case>.json` conforms to `#/$defs/Manifest` and the cross-tool rules: a forwarded domain with
an alias, an operator-keyed domain, a mounted harness that composes, and identity with the entitlement
hook. `Refused.<case>.json` is `{why, manifest}`: the manifest must be refused, for the reason `why`
gives. `../validate.mjs` checks both.
