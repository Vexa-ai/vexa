# zaki_read — bounded Minutes read adapter

Internal, read-only HTTP adapter from the meeting-api `TranscriptStore` to the sealed
`zaki-read.v1` contract. The module owns token/path/header agreement, privacy and retention
filtering, metadata pagination, item projection, response byte bounds, and non-enumerating errors.

Public surface: `build_router`. It may depend on the meeting-api collector port and the sealed
`core/meetings/contracts/zaki-read.v1` profile. It does not write meeting data, Brain data, or
deployment state. With no injected read token, every request fails closed; charts and activation
remain separate work.
