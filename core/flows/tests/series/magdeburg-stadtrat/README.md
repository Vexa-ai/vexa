# magdeburg-stadtrat — Stadtrat der Landeshauptstadt Magdeburg

**Why this series.** Formal, longitudinal, institutional German, with a *Niederschrift* published
for every single session in the city's SessionNet Ratsinformationssystem. The minutes are
**Verlaufsprotokolle** — structured records of agenda items, motions, named speakers with their
faction, vote counts and resolution numbers. They name every attendee and every absentee.

**Read the limit before using this.** A Niederschrift is a *summary*, not a stenographic record.
It is excellent ground truth for **who was there, which faction they speak for, what the agenda
was, what was decided and by what margin, and which threads run across sessions** — which is
exactly what scaffold inference has to get right. It is **not** ground truth for word-level
transcription accuracy, and nothing in this fixture should be used to compute one.

A second limit, and it cuts the other way from the usual one: the acoustic and social domain is a
council chamber — one speaker at a rostrum, a PA feed, heavy Amtsdeutsch — not a VC call. What
transfers is the *language* and the *longitudinal structure*, not the audio conditions.

## Episodes

| # | Date | Session | Video | Niederschrift | Source length | In this fixture |
|---|---|---|---|---|---|---|
| 1 | 2026-01-22 | 028.(VIII) | [faCdTaUt5Eg](https://www.youtube.com/watch?v=faCdTaUt5Eg) | [PDF](https://ratsinfo.magdeburg.de/getfile.asp?id=736261&type=do) · [session](https://ratsinfo.magdeburg.de/si0057.asp?__ksinr=124454) | 6h53 | **first 35 min** |
| 2 | 2026-02-26 | 030.(VIII) | [0Dq9t1XDmVU](https://www.youtube.com/watch?v=0Dq9t1XDmVU) | [PDF](https://ratsinfo.magdeburg.de/getfile.asp?id=738820&type=do) · [session](https://ratsinfo.magdeburg.de/si0057.asp?__ksinr=124457) | 6h26 | **first 35 min** |
| 3 | 2026-03-26 | 032.(VIII) | [q9f5fX7vEcU](https://www.youtube.com/watch?v=q9f5fX7vEcU) | [PDF](https://ratsinfo.magdeburg.de/getfile.asp?id=740939&type=do) · [session](https://ratsinfo.magdeburg.de/si0057.asp?__ksinr=124459) | 6h44 | **first 35 min** |

**The trim matters here more than in the English series.** These sessions run 6–7 hours; the
fixture holds the first 35 minutes of *video*, and each stream starts roughly 9–10 minutes before
the session is gavelled in, so each fixture covers only the **first ~25 minutes of actual
session** — the opening, the roll, and the fight over the agenda. The ground-truth files are
written to that window and say where they stop. Everything after it lives in the linked PDF and
is deliberately out of scope; a scaffold is not wrong for missing it.

That window is not filler. In this council the agenda fight *is* the politics: which motion gets
deferred, which gets pulled into the non-public part, whether a two-thirds majority was actually
reached. Episode 3's first twenty minutes are a procedural battle over whether the Intel-site
motion may be added at all, decided by a re-run vote after the Oberbürgermeisterin challenged the
count.

## Who is in the room

Roles the scaffold has to learn (they recur verbatim every session, which is the point):

- **Vorsitzender des Stadtrates** — Wigbert Schwenke, who chairs; 1. stv. Dr. Norman Belas,
  2. stv. Stephan Bublitz
- **Oberbürgermeisterin** — Simone Borris (Frau Borris), the executive, frequently *opposed* to
  the council she attends
- **Beigeordnete** — the departmental heads, addressed by portfolio, not by name
- **Fraktionen** — CDU/FDP-Stadtratsfraktion · SPD/Tierschutzallianz/Volt · AfD · Die Linke ·
  GRÜNE/future! · Gartenpartei · Tierschutzpartei
- **Landesverwaltungsamt** — the state supervisory authority; not in the room, and the single
  most consequential actor in the series

Full per-session rosters (present and excused, by name) are in each Niederschrift under
`Anwesend:` / `Abwesend - entschuldigt`.

## Running threads

- **Haushaltssperre / Haushaltskonsolidierung** — episode 1: the Landesverwaltungsamt approves the
  budget with conditions (≈5.3 M€ investment cut, 7.14 M€ commitment-authorisation cut, immediate
  spending freeze, 11 M€ to be saved, consolidation concept due **30 November 2026**). Recurs in
  episode 2.
- **Intel-Gelände / High-Tech Park** — episode 2 (motion pushed into the non-public part),
  episode 3 (interfractional motion to buy the Eulenberg site back; the Oberbürgermeisterin wants
  a special session instead).
- **Organisationshoheit** — episode 1's Widerspruch: the executive formally objects to a council
  resolution as an intrusion on her authority, and warns she may have to do it again.
- **Magdeburg2040** — the administration's strategy paper, a Grundsatzaussprache in episode 3.
- **Mitwirkungsverbot** — episode 3 opens with a warning about conflict-of-interest recusal,
  prompted by a neighbouring town's resolution being struck down.

## Ground truth

`ground-truth/ep<N>.md` — **DERIVED**. Distillations *written by us* from the Niederschrift PDFs
linked above, scoped to the trimmed window. The `## Entities` section is what
`series_run.py judge` reads for its presence check. Where derivation and PDF disagree, the PDF
wins.

## Provenance and copyright

Public sessions of a German city council, livestreamed by the city on its own channel, with
minutes published by the city in its own Ratsinformationssystem. Captions obtained with
`tests/series/fetch.py` (`yt-dlp --write-auto-sub --skip-download` — captions only), trimmed as
recorded above. **Internal test fixtures. Not for republication.** Note that citizens speak in
the public *Einwohnerfragestunde*; derived transcripts of named private individuals are not
something to publish without a legal read that has not been done.
