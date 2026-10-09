# routes.v1 goldens

`Manifest.<case>.json` conforms to `#/$defs/Manifest` and the cross-row rules: the edge's own unscoped
routes, a forwarded domain with `verbs` rows, and a domain with named rows. `Refused.<case>.json` is `{why, manifest}`:
the manifest must be refused, for the reason `why` gives. `../validate.mjs` checks both.
