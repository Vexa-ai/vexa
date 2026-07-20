### Fixed

**A Zoom speaker handover now closes the outgoing speaker's hint window.** Zoom's adapter reports
who is lit *now*, and the bridge emitted an end hint only when nobody was lit — so a direct A→B
handover, which is what a real meeting does, opened B without ever closing A. On a live 5-person
call that produced 142 hints with **zero** end events across 13 handovers, and two of the five
participants reached the transcript: a speaker whose window never closes keeps winning the
max-overlap vote against everyone who follows.
