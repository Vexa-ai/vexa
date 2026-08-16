# tests

- **`test_gate.py`** — every branch of the three outcomes, on synthetic records built to
  the shapes measured from the calibration corpus. Always runs.
- **`test_corpus.py`** — replays the real 22-record corpus when `VEXA_PRESEND_CORPUS`
  points at an operator-held copy, and skips otherwise. The corpus is **private and
  deliberately not vendored**: two of its records are the private material this gate
  exists to keep off an email thread.
- **`conftest.py`** — the synthetic record builders.
