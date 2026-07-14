# zaki-read.v1 goldens

Synthetic, non-personal examples for the Minutes read boundary. A filename starts with the `$defs`
shape it targets. Files containing `.invalid.` are independent negative controls; all other JSON
files must conform. Privacy controls change one invariant at a time so one constraint cannot mask
another; turn ordering and range are enforced by `validate.mjs` after JSON Schema conformance.
