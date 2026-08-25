# nodejs-tsc — Node.js Technical Steering Committee, weekly

**Why this series.** It is the rarest thing in public meetings: an organizer who binds the notes
to the recording themselves. Every minutes file in
[`nodejs/TSC/meetings/`](https://github.com/nodejs/TSC/tree/main/meetings) opens with an explicit
`**Recording**: <youtube url>` line, then a `## Present` roster with **full names and GitHub
handles**, then **speaker-attributed** discussion (`Fedor: …`, `Matteo: …`). So there is no
date-matching guesswork, and the ground truth covers all four things the scaffold has to get
right at once — who the people are, what the projects are, what the vocabulary means, and which
threads are still running.

It is also genuinely longitudinal. The AI-contributions vote
([nodejs/TSC#1831](https://github.com/nodejs/TSC/issues/1831)) is announced in episode 1, is
scheduled in episode 1 *for* episode 3, and consumes episode 3. `Ownership of Rust crates`
([nodejs/admin#1045](https://github.com/nodejs/admin/issues/1045)) appears in all three. A
scaffold that only reads one episode cannot know either of those is a thread; that is the point.

## Episodes

| # | Date | Video | Organizer notes | Source length | In this fixture |
|---|---|---|---|---|---|
| 1 | 2026-03-18 | [FUHLCNaeVu4](https://www.youtube.com/watch?v=FUHLCNaeVu4) | [2026-03-18.md](https://github.com/nodejs/TSC/blob/main/meetings/2026-03-18.md) | 24:56 | full |
| 2 | 2026-03-25 | [teyBqlrCaso](https://www.youtube.com/watch?v=teyBqlrCaso) | [2026-03-25.md](https://github.com/nodejs/TSC/blob/main/meetings/2026-03-25.md) | 49:51 | **first 40 min** |
| 3 | 2026-04-01 | [Jzw4D2MqAXY](https://www.youtube.com/watch?v=Jzw4D2MqAXY) | [2026-04-01.md](https://github.com/nodejs/TSC/blob/main/meetings/2026-04-01.md) | 54:54 | **first 40 min** |

Episodes 2 and 3 are **trimmed to the first 40 minutes** to keep the library small. Episode 3's
trim cuts the tail of the AI-contributions debate; the ground-truth file records the whole
discussion, so a scaffold will legitimately look thinner than the notes on the last items. That
is a known asymmetry, not a scaffold failure — read it that way when judging.

## Participants

From the `## Present` rosters (these are the *published* attendees; the transcript is
auto-captioned and carries **no speaker labels at all**, so nothing in the fixture attributes a
line to a person):

Antoine du Hamel (@aduh95) · Matteo Collina (@mcollina) · Filip Skokan (@panva) ·
Robert Nagy (@ronag) · Michaël Zasso (@targos) · Marco Ippolito (@marco-ippolito) ·
Joyee Cheung (@joyeecheung) · Chengzhong Wu (@legendecas) · Richard Lau (@richardlau) ·
Ruy Adorno (@ruyadorno) · Paolo Insogna (@ShogunPanda) · Beth Griggs (@BethGriggs) ·
Ruben Bridgewater (@BridgeAR) · James Snell (@jasnell) · Rafael Gonzaga (@RafaelGSS) ·
Jacob Smith (@JakobJingleheimer, guest) · Fedor Indutny (@indutny, guest, TSC emeritus) ·
Joe Sepi (@joesepi, CPC rep) · Maël Nison (@arcanis, guest) · Robin Ginn (OpenJS ED)

## Ground truth

`ground-truth/ep<N>.md` — **DERIVED**. Each file is a distillation *written by us* from the
organizer's published minutes linked above, not the minutes themselves. The `## Entities` section
is what `series_run.py judge` reads for its presence check. When the two disagree, the linked
minutes win.

## Provenance and copyright

Public project meetings of an OpenJS Foundation project, recorded and published by the project on
its own channel, with minutes published by the project in its own git repository. Captions were
obtained with `tests/series/fetch.py` (which runs `yt-dlp --write-auto-sub --skip-download` —
captions only; no audio or video is ever downloaded), trimmed to what the harness needs. These
are **internal test fixtures**. Not for republication.

## Known weakness

The minutes practice appears to stop after **2026-05-06** — recordings continue with no committed
minutes. Treat this as a fixed historical corpus, not a renewable feed. (Unverified whether that
is a lapse, a lag, or a process change.)
