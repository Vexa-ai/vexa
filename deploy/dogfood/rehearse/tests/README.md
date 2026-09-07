# tests — offline by construction

No network, no docker, no home directory: the doors are substituted with `stub_doors.StubDoors`
and the transcripts come from `fixtures/`. The whole suite runs in under a second, which is what
makes it something you run before every change rather than instead of one.

| file | what it holds to account |
|---|---|
| `test_catalogue.py` | the recipes validate themselves, and the `door:` column is proved not to be decoration |
| `test_guards.py` | the two refusals that keep a rehearsal off the founder's data, wired end to end |
| `test_engine.py` | every state runs and passes its own verify block; idempotence; derived addresses |
| `test_reset.py` | `subject_reset` removes one subject, reads the emptiness back, and reports what it could not |
| `test_runner.py` | `runner=` binds a harness per subject — and to nobody else |
| `test_hot.py` | **no state needs an image** (PRD decision 38.4), asserted over the source rather than promised |
| `test_run_all.py` | the catalogue as the test, and what it files when a state breaks |

The other half of the runner chain lives in `core/agent/tests/test_runner_per_subject.py`: this
suite proves the exact config a rehearsal writes, that one proves the exact worker env it becomes.
Neither can prove the other, and a test that mocked across the seam would prove neither.
