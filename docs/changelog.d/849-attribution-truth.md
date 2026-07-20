### Added

**Speaker attribution can be scored for correctness without a meeting.** `speech_fixture.py --lane
mixed` builds a multi-speaker session from known-text TTS clips with the names withheld from the
audio and delivered as a DOM hint stream — with dials for how late the UI notices a speaker, how
often it says nothing, and how often it says the wrong name. `TRUTH_JSON=` then scores the published
transcript against who actually spoke, so "is the name right?" is a command rather than a meeting.
