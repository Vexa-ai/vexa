# zaki_read — bounded Minutes read adapter

Internal, read-only HTTP adapter from the meeting-api `TranscriptStore` to the sealed
`zaki-read.v1` contract. The module owns token/path/header agreement, privacy and retention
filtering, metadata pagination, item projection, response byte bounds, and non-enumerating errors.

Public surface: `build_router`. It may depend on the meeting-api collector port and the sealed
`core/meetings/contracts/zaki-read.v1` profile. It does not write meeting data, Brain data, or
deployment state. With no injected read token, every request fails closed; charts and activation
remain separate work.

## Retention behavior

The adapter consumes explicit UTC expiry instants already materialized by the policy authority. It
does not choose retention defaults. Audio, transcript and summary are independent scopes; a scope
whose expiry is due, missing or marked expired is not readable. A transcript summary fallback
requires both its transcript and stored-summary scopes to remain live.

Policy may change for future meetings at any time. For an existing meeting, the retention core may
keep or shorten a stored expiry but never move it later. Reads do not renew deadlines. Expiry,
meeting deletion and account erasure can remove data earlier than policy, and expired or erased
data must not return through reads or restoration.

The proposed launch policy is 7 days for audio, 90 days for transcripts, and a summary window no
longer than its transcript. These are unratified product-policy proposals, not engine defaults.
The intended authority model lets a user choose a shorter duration within an operator maximum;
operator endpoints, credentials, activation and policy ceilings are never user configuration.

## Read bounds

| Surface | Default | Maximum |
|---|---:|---:|
| Index page | 50 items | 200 items |
| Search page | 20 items | 50 items |
| Serialized item content | — | 262,144 bytes |
| Complete serialized item response | — | 270,336 bytes |

An oversized full item returns `413`. `variant=summary` uses a bounded stored summary and does not
load transcript segments. Index and search contain metadata only; continuation uses opaque signed
cursors bound to the user, route, query controls and evaluation snapshot.

There is no special 8-call/1-MiB cross-spoke budget. Per-page/per-item limits, request timeouts,
issued IDs/cursors, and ordinary Agent iteration and billing controls provide the volume boundary.
