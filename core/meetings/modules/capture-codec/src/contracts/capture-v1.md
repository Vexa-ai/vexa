# capture.v1 — extension/bot → desktop capture wire

The ONE serialization shared by both capture lanes (`gmeet-capture.v1`,
`mixed-capture.v1`). The **sender** stamps capture-time into every frame; the
**receiver NEVER restamps** (uses `ts` as-is, never `Date.now()`). Pure,
zero-dep, drift-gated — bot-captured and extension-captured fixtures are
byte-identical. This contract is the source of truth; the rest of this page is
the **validation index** — the one place that says where every piece is proven.

The codec lives in [`../index.ts`](../index.ts). Two frame kinds cross the seam:
a **binary audio frame** and a **text event frame**. (The `REC1` recording-chunk
frame also rides this same ingest WS, but it belongs to `recording.v1` and is
golden-pinned in `modules/recording` — it is NOT part of this contract.)

## Audio frame (binary) — `encodeAudioFrame` / `decodeAudioFrame`

Little-endian throughout. Two **backward-compatible** shapes, told apart by the
high bit of the leading Int32 `track`:

**Unnamed (mixed lane)** — high bit clear:

| offset | type        | field                                  |
|--------|-------------|----------------------------------------|
| 0      | Int32LE     | `track` (`speakerIndex`, ≥ 0)          |
| 4      | Float64LE   | `ts` (CAPTURE epoch ms)                |
| 12     | Float32LE…  | PCM samples (16 kHz mono), `n` floats  |

Header = **12 bytes**, then `4·n` PCM bytes.

**Named (gmeet lane)** — high bit (`0x80000000`) set on `track`:

| offset | type         | field                                              |
|--------|--------------|----------------------------------------------------|
| 0      | Int32LE      | `track | 0x80000000` (decode masks with `0x7fffffff`) |
| 4      | Float64LE    | `ts` (CAPTURE epoch ms)                            |
| 12     | Int32LE      | `nameLen` (UTF-8 byte length of the name)          |
| 16     | UTF-8 bytes  | `speakerName`, then **zero-padded to a 4-byte boundary** |
| 16+pad | Float32LE…   | PCM samples, `n` floats                            |

Header = **16 bytes** + `padded` name bytes (`padded = (nameLen + 3) & ~3`,
keeping the PCM 4-byte aligned), then `4·n` PCM bytes.

The high bit is **never** set by a real track id (`0..1000`), so a legacy
(unnamed) frame decodes unchanged — that is the back-compat rule. `gmeet` binds
the glow name HERE at the source; `mixed` omits it and names downstream from
hints. Decoded shape:
`{ speakerIndex, ts, samples: Float32Array, speakerName? }` (`speakerName`
present only on named frames).

**PCM precision** — the wire stores Float32. The decoded `samples` are exactly
the input rounded to Float32 (bit-identical to `new Float32Array(input)`), not
the original Float64 literals.

## Event frame (text) — `encodeEvent` / `decodeEvent`

`JSON.stringify(MeetingEvent)` ⇄ `JSON.parse` with a shape guard. The envelope
(chat + lifecycle + the mixed lane's active-speaker hints all ride this one
JSON shape):

| field     | type   | meaning                                                              |
|-----------|--------|---------------------------------------------------------------------|
| `kind`    | string | `speaker-joined`·`speaker-left`·`active-speaker`·`caption`·`segment`·`lifecycle`·`track-lock`·`chat` |
| `ts`      | number | CAPTURE epoch ms                                                     |
| `speaker` | string?| active-speaker name / chat sender display name                      |
| `text`    | string?| caption / segment / chat text                                       |
| `detail`  | object?| e.g. active-speaker → `{ hint, isEnd, index }`                       |

`decodeEvent` returns `null` (never throws) when the JSON is malformed, or when
`kind` is not a string or `ts` is not a number.

## Validation — golden-pinned, byte-identity

The vectors in [`golden/`](golden/) pin both directions of both frame kinds:
each carries the encoded **bytes (base64) + sha256 + len** AND the decoded
struct, so `encode(input)` is asserted byte-identical to the golden bytes AND
`decode(goldenBytes)` is asserted to round-trip back to the input struct.

| level | home | command | proves |
|-------|------|---------|--------|
| **contract** | `golden/generate.mjs` | `npx tsx src/contracts/golden/generate.mjs --check` | the committed vectors *are* what the codec emits (tamper-evident, reproducible) |
| **module** | `src/capture-v1-golden.test.ts` | `pnpm --filter @vexa/capture-codec test` | `encode`/`decode` ≡ vectors (base64 + sha256 + len + struct round-trip) |
| **module** · REC1 framing | `src/recording-chunk.test.ts` | same `test` | the `recording.v1` `REC1` frame round-trips and is disambiguated from audio (recording.v1's delta, not capture.v1) |

Enforced in CI by `.github/workflows/gates.yml` → `pnpm test` (turbo runs each
package's `test` script, including this one).

## Definition of done

> capture.v1 is green when `pnpm --filter @vexa/capture-codec test` passes — the
> golden test asserts byte-identity of every committed vector in both
> directions — and `npx tsx src/contracts/golden/generate.mjs --check` confirms
> the vectors still match the codec.

## Changing the wire

The vectors are the spec. To change a frame layout:

1. Edit the codec ([`../index.ts`](../index.ts)).
2. Regenerate: `npx tsx src/contracts/golden/generate.mjs` (rewrites the JSON).
3. Review the new `sha256`/`len`/`bytes` in the diff — they are the proof the
   change is intentional. `--check` fails in CI until the committed vectors
   match the codec again.

Because a published `.vN` is FROZEN, any wire change is either a new version or
a back-compat-only change (the high-bit named-frame flag is exactly such a
back-compat extension of the original unnamed layout).
