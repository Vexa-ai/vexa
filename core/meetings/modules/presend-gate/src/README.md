# `src/` — the `presend_gate` package

One package, four modules, stdlib only:

| Module | Concern |
|---|---|
| `record.py` | the normalized `MeetingRecord` + the meeting-api transcript adapter |
| `signals.py` | measurement — numbers and bools, no judgement |
| `policy.py` | judgement — the three outcomes, the thresholds, the recipient hook |
| `lexicon.py` | word-level probes (en/ru/de), contributory only |
| `report.py` | `python -m presend_gate.report <dir>` — verdicts + signals as a table |
