# Zoom offline fixtures

Offline metadata fixtures owned by the Zoom join implementation in
`@vexa/join`. They define deterministic negative controls without storing
meeting content or contacting Zoom during tests.

`official-test-page.v1.json` names Zoom's public `https://zoom.us/test` page and
the page/capability/cleanup observations required later from a native stock
image. It contains no fetched HTML, credentials, meeting identifier, passcode,
participant data, or screenshot. The fixture forbids participant traffic and
cannot prove protected-room admission, CAPTCHA bypass, or universal unsigned
Zoom support.

Update this fixture with its package-local test. A real page observation must
record its source SHA, image digest, architecture, fresh-profile custody, and
cleanup separately; it must never be folded into this static fixture as if it
were current runtime evidence.
