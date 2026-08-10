# Goldens

Committed request and decision vectors for `service-authority.v1`. The prefix
before the first dot names the `$defs` shape validated by `validate.mjs`.

Cross-shape invariants are enforced by the consumer because JSON Schema cannot
bind a standalone Decision golden to its Request: admission has no stop scope;
a denied continuation stops `billable_service`.
