# routes.v1 goldens

`Manifest.<case>.json` conforms to `#/$defs/Manifest` and the cross-row rules: the edge's own unscoped
routes, a forwarded domain with `verbs` rows, a domain with named rows, and a forwarded domain that declares `shared` (D3) on every row and verb. `Refused.<case>.json` is `{why, manifest}`:
the manifest must be refused, for the reason `why` gives. `../validate.mjs` checks both.
