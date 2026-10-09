- **Native SDK path: optional, operator-supplied, not supported (#1772).** The source tree carries the
  seam for an optional native meeting backend: the sealed `sdk-join.v1` and `sdk-capture.v1`
  contracts, the `@vexa/join/node` port and the `@vexa/zoom-sdk-capture` adapter. No image, Compose,
  Helm or Lite install contains the SDK or starts the path, and Vexa makes no support claim for it.
  An operator who wants it downloads the SDK from Zoom under Zoom's own terms. P17 treats it as an
  optional runtime, not a dependency (ADR-0039), and `gate:vendor-payload` keeps it out of everything
  Vexa ships.
