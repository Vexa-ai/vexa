# meeting-bundle.v1 / golden

The spec, as examples (P8). `../validate.mjs --check` holds every file here; meeting-api's
`tests/test_meeting_bundle_contract.py` regenerates the zips from the shipped code and requires
identical bytes.

- `<Shape>.<case>.json` — one instance of `#/$defs/<Shape>` (the parts of `bundles/with-audio.zip`,
  and one refusal body).
- [`bundles/`](bundles/) — bundles every importer must accept.
- [`refused/`](refused/) — bundles every importer must refuse, with the code `refused/refused.json` names.
