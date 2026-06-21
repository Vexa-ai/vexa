# golden — captured-signal.v1 vectors

Conforming examples (the spec, P8). Filename = `<Shape>.<case>.json`; the prefix before the first
dot is the `$def` the vector must conform to (`CapturedFrame`, `SessionHeader`). `pcm` is base64 of
a deterministic Float32-exact PCM ramp (`n/256`), so every frame round-trips through
`@vexa/capture-codec` bit-exactly. Covered: a gmeet glow-named frame, a mixed-stream frame, a mixed
frame with an active-speaker hint, and a session header.
