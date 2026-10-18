# fixtures

`face-cases.json` — a verbatim copy of blocks.v1's golden `FaceCases.resolve.json`
(`core/agent/contracts/blocks.v1/golden/`), held identical by the parity fact `card-face-cases`
(`scripts/parity.json`). `test_cards.py` resolves every case with the vendored `card_face.py`. Never
edit it here: regenerate the contract's golden and copy it over.
