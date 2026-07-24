# Zoom producer DOM trace fixtures

These JSONL fixtures use the strict, platform-local `producer_dom_trace.v1`
contract. Zoom rows preserve every exclusive `dom-active` poll at the declared
250 ms cadence with canonical view/footer enums and fixed pseudonym tokens only.

`provenance:"authored"` means the fixture is deterministic coverage, not a live
DOM capture. A captured fixture must be pseudonymized in-page, pass the local
capture station and package parser, and must never contain raw names, participant
IDs, DOM text, attributes, classes, URLs, or meeting identifiers.
