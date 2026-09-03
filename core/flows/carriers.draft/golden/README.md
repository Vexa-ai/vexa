# `golden/` — one file per registered carrier

A golden's filename is `<Shape>.<case>.json`; the part before the first dot is the `$def` it must
conform to, so `Carrier.onboarding-completed.json` is validated against `#/$defs/Carrier`. That is
the same convention every other contract in this repo uses, and `validate.mjs` is the same script.

These are not test fixtures. Each file **is** the registration: the carrier exists because there is
a golden for it, and a fact published with no file here is an undeclared coupling between two
domains.
