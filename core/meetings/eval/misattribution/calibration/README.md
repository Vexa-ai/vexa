# calibration

Measured performance of the judge + scorer against meetings whose true track
ownership is known independently of this tool.

[`REPORT.md`](REPORT.md) is the current run: the two witnessed meetings of
[#1221](https://github.com/Vexa-ai/vexa/issues/1221), the corpus inventory
behind the first sweep, and the honest statement of what these two signals
cannot see.

Regenerate with `calibrate.py` against `../truth/<meeting>.json`. A sweep is
only meaningful after a calibration run reports flagged precision 1.0 — a
judge that flags wrongly makes the fixture set worse than no fixture set.
