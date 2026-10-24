# demo — the scripts a demo call plays

_bot · data · invocation.v1 `call.presenter` · `../src/presenter.ts`._

A demo call (`POST /demo-meetings` on meeting-api) is a call room nobody hosts. The listener that
transcribes it also joins once per scripted speaker and plays one of the scripts in this directory,
so the person watching has a live transcript within seconds. Each script is a directory named as
`call.presenter.script` names it:

| File | What |
|---|---|
| `script.json` | `script` (its own name), `sampleRate`, `leadInMs`, `gapMs`, `speakers` (the voices, as the transcript names them) and `turns` (`speaker`, `audio`, `text`) |
| `turnN.wav` | one turn's speech: 16-bit mono PCM at `sampleRate` |

meeting-api mints one publish token per name in `speakers` (`meeting_api/demo/router.py`
`DEMO_SPEAKERS`); the bot refuses a script whose voice has no token. `src/presenter.test.ts` loads
every bundled script.
