# Join contract fixtures

Offline acceptance fixtures for contracts owned by `@vexa/join`. They describe
decisions the join boundary must make before an embedder launches a browser;
they are not captured pages, browser binaries, or final-image evidence.

`browser-product.contract.v1.json` records the #938 prepared fork: unsigned
stock Zoom selects Playwright 1.61.1 with Firefox 151 revision 1532 and a fresh
ephemeral profile, while authenticated Zoom and non-Zoom platforms retain the
existing Chromium lane. Its artifact paths, notice requirements, native-image
matrix, and Chrome-for-Testing exclusions come from the public P17 decision
packet on #938; the arm64 source-image digest is explicitly research
provenance, not proof for another architecture.

Ownership stays in this package. Change the fixture, `browser-product.ts`, and
`browser-product.test.ts` together. Runtime wiring, image assembly, licence
files, and SBOM emission remain owned by their respective service/deployment
boundaries and require their own evidence.
