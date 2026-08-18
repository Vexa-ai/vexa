# misattribution — attribution errors as a measured fleet metric

Transcripts self-incriminate. This is the machinery that reads that confession
and turns it into a fixture set the attribution train can regress against.

Refs [#1224](https://github.com/Vexa-ai/vexa/issues/1224),
[#1221](https://github.com/Vexa-ai/vexa/issues/1221).

## The two signals, and why only two

| signal | shape | what it proves |
|---|---|---|
| **vocative** | a segment addresses a participant by name — "thanks, P2", "P2, let me interrupt you", "so nice to meet P2" | whoever spoke it is **not** P2 |
| **self_id** | the speaker names themselves — "I'm P2", "this is P2", "P2 here" | whoever spoke it **is** P2 |

Nothing else is admitted. Not turn-taking logic, not register or style, not
"this name never appears so the track can't be theirs". Those produce plausible
flags, and a plausible flag inside a regression gate is worse than no flag at
all: it makes the metric move for reasons unrelated to the pipeline. The
scope guard is a ruling, not a tuning parameter — widening it is a doctrine
change.

The price is recall, and the calibration below measures exactly what that price
is. A backchannel — "yeah, and", "so it's good" — carries no name and is
therefore invisible to this method no matter how badly it is attributed. That is
the honest ceiling of linguistic self-incrimination, and it is why tape-backed
mechanical verification exists alongside it.

## Shape of the pipeline

```
transcript.jsonl ─► pseudonymize ─► judge (LLM, label-blind) ─► flags.jsonl
                          │                                        │
                     name map                    labels ──► scorer ─┴─► verdicts
                  (stays off-repo)                                      │
                                          ground truth ──► calibrate ───┤
                                                                        ▼
                                                                    manifest
```

Two properties carry the whole design:

**The judge never sees the rendered speaker label.** It gets segment ids and
pseudonymized text and nothing else. The scorer performs the join afterwards.
If the judge saw the label it would be scoring its own prior, and agreement
between judge and pipeline would prove nothing.

**Only the scorer declares an error, and the scorer is deterministic.** Same
flags plus same transcript produce the same verdicts byte-for-byte, which is
what makes this usable as a gate rather than as an opinion.

## Privacy boundary

The name map — real name → `P1`, `P2` — is written to a path you choose and
**must stay outside the repository**. Everything downstream of `prepare` is
pseudonymous: judge output, verdicts, calibration numbers, and the committed
manifest carry ids, pseudonyms, signal types and span *lengths* only. No
transcript text and no real name is ever committed.

Pseudonymization is deliberately over-eager. Rosters arrive from three sources
at once — the DOM label, the calendar attendee, the email local-part — so one
human shows up as `Dmtiry Grankin`, `dmitry` and `Dmitry Grankin` in the same
meeting; spellings that denote the same person are merged onto one pseudonym,
and in-text mentions are matched fuzzily (transposition typos, given-name
variants like Alex/Alexander). Under-merging is the dangerous direction: it
splits one human across two pseudonyms and the vocative test then silently
finds nothing.

## Run it

```bash
cd core/meetings/eval/misattribution
M=26424

# 1. pseudonymize + build the label-blind prompt bundle
python3 judge.py prepare \
  --transcript $WORK/$M/transcript.jsonl \
  --attendees  $WORK/$M/attendees.json \
  --name-map   ~/.vexa-namemaps/$M.json \
  --out        $WORK/$M/bundle

# 2. run the judge (ANTHROPIC_API_KEY; or --backend cli / --backend prepared)
python3 judge.py run --bundle $WORK/$M/bundle --model claude-opus-5

# 3. normalize + validate the model output
python3 judge.py ingest --bundle $WORK/$M/bundle \
  --transcript $WORK/$M/transcript.jsonl \
  --name-map ~/.vexa-namemaps/$M.json --out $WORK/$M/flags.jsonl

# 4. deterministic scoring
python3 score.py --transcript $WORK/$M/transcript.jsonl \
  --flags $WORK/$M/flags.jsonl --name-map ~/.vexa-namemaps/$M.json \
  --out $WORK/$M/verdicts.json

# 5. measure against known truth (calibration meetings only)
python3 calibrate.py --transcript $WORK/$M/transcript.jsonl \
  --verdicts $WORK/$M/verdicts.json --truth truth/$M.json \
  --name-map ~/.vexa-namemaps/$M.json
```

`transcript.jsonl` is the `segments` array of
`GET /transcripts/by-id/{meeting_id}` — one segment per line, or the whole
response document. The virtual-track key is the `segment_id` prefix
(`csrc-201:1:… → csrc-201`, `ch-0:81:… → ch-0`).

Backends for step 2:

| backend | use |
|---|---|
| `anthropic` | default; `ANTHROPIC_API_KEY` + the Messages API |
| `cli` | `claude -p` headless, when only a CLI session is available |
| `prepared` | responses already on disk — the deterministic replay path. Same prompts in, same manifest out, no model call. |

`ingest` structurally validates every row before the scorer sees it: unknown
segment id, unknown pseudonym, signal/direction mismatch, or a `quote_span` that
is not verbatim in the segment all drop the row. A hallucinated flag cannot
reach a verdict.

## Tiering

| tier | meaning |
|---|---|
| **gold** | the linguistic signal and the session tape agree — the tape carries `csrc` frames and `observations`, so track separation and roster timing replay offline and independently confirm the verdict |
| **silver** | linguistic signal only — either no tape survives, or the lane records no cross-check signal |

The GMeet channel lane writes **no** `csrc`/`observations` at all (the capture
gap recorded in #1221), so every GMeet fixture caps at silver until that lane
records cross-check signal. That cap is a finding about the capture path, not a
limitation of the judge.

## Fleet metric

The manifest is the denominator. For a release, lane and platform:

```
attribution_error_rate = mislabeled_tracks / tracks_evaluated
```

counted over the fixture set, per `platform` × `lane` × release. A candidate
that flips a `MISLABELED` fixture to `CLEAN` without introducing a new one has
improved attribution; anything else is a claim without a number behind it.

## Files

| file | role |
|---|---|
| `pseudonymize.py` | roster identity merging + in-text name redaction |
| `judge.py` | prompt construction, the three backends, output validation |
| `score.py` | deterministic flags × labels → per-track verdicts |
| `calibrate.py` | verdicts vs known ground truth → precision / recall |
| `build_manifest.py` | verdicts → content-free fixture manifest |
| `manifest.schema.json` | the manifest contract |
| `test_score.py` | unit tests for the evidence algebra |
| `fixtures/manifest.json` | the current fixture set |
| `calibration/REPORT.md` | measured calibration + the corpus inventory |
