# Bot regression fixtures

Sanitized deterministic inputs for bot boundary tests. `zoom-clock.json` describes
an eight-track capture schedule with callbacks advancing at 43% of wall time.
The real bridge is exercised by `zoom-speaker-wiring.test.ts`; PCM is generated
from the declared amplitude. This tests timing, not speech recognition or audio recovery.
