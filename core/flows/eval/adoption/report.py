"""Assemble the revolution's REPORT.md from what was actually measured.

Everything printed here comes from a file written by a run — rates.json (Haiku over real mail),
conversations.json (the real agent), and the sim over the generated org. Nothing is typed in.
"""
from __future__ import annotations

import json
import os
import sys

import org as O
import personas as P
import sim as S

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r1"))
SIZES = [int(x) for x in os.environ.get("SIM_SIZES", "2000,20000,200000").split(",")]
LEVERS = ["off", "shared", "personal"]
LEVER_NAME = {"off": "null — organizer only (the line today)",
              "shared": "A — one shared follow-up to every attendee",
              "personal": "B — per-person, same single agent turn"}


def fmt(v, pct=False):
    if v is None:
        return "—"
    return f"{v*100:.1f}%" if pct else str(v)


def main():
    rates = S.Rates.load(f"{RUN}/rates.json")
    raw = json.load(open(f"{RUN}/rates.json"))
    touches = json.load(open(f"{RUN}/touches.json"))
    convs = []
    if os.path.exists(f"{RUN}/conversations.json"):
        convs = json.load(open(f"{RUN}/conversations.json"))

    out = []
    w = out.append

    w("# Adoption simulator — revolution 1\n")
    w(f"**Run:** `{RUN}` · **Branch:** `adoption-sim` · **Base:** `544ceb17d` · "
      "**Issue:** [biz#449](https://github.com/DmitriyG228/biz/issues/449)\n")
    w("**Agent under test:** Haiku on both sides — the simulated persona, and the deployed "
      "product agent where it was exercised.\n")
    w("> **The number is relative between revolutions. It is never a forecast.** It exists to "
      "rank changes against each other, and it is only as good as the rates measured beneath "
      "it. Read every absolute figure as *this lever, against this null, on this org shape*.\n")

    w("## 0. Real vs simulated\n")
    w("| | real | simulated |")
    w("|---|---|---|")
    w("| flows engine, steps, retries, mail delivery | the deployed engine on `bbb` | |")
    w("| the mail text every decision was made on | verbatim out of mailpit | |")
    w("| the note and the per-attendee blocks | one real agent turn per meeting | |")
    w("| the chat after a click | the deployed agent over agent-api | |")
    w("| the people, personas and decisions | | Haiku, one call per touch |")
    w("| the org, its meeting graph, the calendar | | generated, SPI-shaped |")
    w("")
    w("**Active** is strict, per the founder's tightening: in the trailing 14 days the person "
      "**opened** a Vexa mail **and** took a **UI action** — clicked into the terminal, sent a "
      "chat turn, replied to the mail, or put the mailbox on a meeting they own. Delivered mail "
      "scores zero; an open alone is *reached*, not active.\n")

    # ── the org
    o2 = O.build("spi", SIZES[0]); P.assign(o2)
    st = O.stats(o2)
    w("## 1. The org — SPI, native to the fixtures\n")
    w(f"Working size **{SIZES[0]:,}** (founder's number). *Assumption stated:* public reporting "
      "puts SPI at ~700 **production** staff at peak and Twenty's 5,000 is the Sony Pictures "
      "umbrella — the size is a parameter, not a headcount claim.\n")
    w(f"- {st['people']:,} people · {st['teams']} units · {st['meeting_series']} meeting series "
      f"· {st['meetings_per_week_total']:,.0f} occurrences/week · "
      f"{st['avg_meetings_per_person_per_week']} meetings per person per week")
    w(f"- reachable at all (in any non-external meeting): **{len(set(a for m in o2.meetings if not m.external for a in m.attendees)):,}**")
    w("- dominant recurring meeting by volume: " + ", ".join(
        f"`{k}` {v:.0f}/wk" for k, v in list(st["by_kind_per_week"].items())[:4]))
    w("")
    w("Persona mix (assigned, not asserted): " + " · ".join(
        f"`{k}` {v*100:.0f}%" for k, v in P.mix(o2).items()) + "\n")

    # ── measured rates
    w("## 2. What the personas actually did with the REAL mails\n")
    w(f"Touch kinds harvested from mailpit: {', '.join('`'+k+'`' for k in sorted(touches))}. "
      f"{sum(v['n'] for v in raw['table'].values())} judged decisions; "
      f"{raw.get('judge_errors', 0)} judge errors.\n")
    w("Mail sizes actually sent (this is the A/B, in bytes): " + " · ".join(
        f"`{k}` {len(v['text'])} chars" for k, v in sorted(touches.items())) + "\n")
    w("| persona | touch | opened | UI action \\| opened | → active |")
    w("|---|---|---|---|---|")
    for key in sorted(raw["table"]):
        persona, kind = key.split("|")
        if kind in ("signin",):
            continue
        v = raw["table"][key]
        w(f"| `{persona}` | `{kind}` | {v['open']*100:.0f}% | {v['act_given_open']*100:.0f}% | "
          f"**{v['open']*v['act_given_open']*100:.0f}%** |")
    w("")

    # ── the sims
    w("## 3. T_full, retention, and the lever\n")
    w(f"`T_full` = the day the ACTIVE share crosses **{int(S.FULL_THRESHOLD*100)}%** of the "
      "reachable population. Reported as `>N` when it never crosses inside the horizon — no org "
      "reaches everyone, and a threshold that is only approached asymptotically is not a "
      "measurement.\n")
    w("| size | lever | T_full | peak active | steady state | retention 30 | retention 90 | invited mailbox |")
    w("|---|---|---|---|---|---|---|---|")
    results = {}
    for n in SIZES:
        o = O.build("spi", n)
        P.assign(o)
        for lever in LEVERS:
            r = S.run(o, rates, days=120, attendee_followup=lever)
            results[(n, lever)] = r
            t = r["t_full_days"]
            w(f"| {n:,} | {LEVER_NAME[lever]} | "
              f"{(str(t)+' d') if t else '> '+str(r['days'])+' d'} | "
              f"{fmt(r['peak_active_share'], True)} | {fmt(r['steady_state_active_share'], True)} | "
              f"{fmt(r['retention_30'], True)} | {fmt(r['retention_90'], True)} | "
              f"{r['invited_mailbox']:,} |")
    w("")

    base = results[(SIZES[0], "off")]
    a = results[(SIZES[0], "shared")]
    b = results[(SIZES[0], "personal")]
    w("### H1 against the null\n")
    w("> **H1** — the post-meeting follow-up to every attendee is the mechanism that spreads; "
      "without it adoption travels only through organizers. **Null** — the product spreads "
      "acceptably through organizers alone.\n")
    w(f"At {SIZES[0]:,}: the null peaks at **{fmt(base['peak_active_share'], True)}** active and "
      f"settles at **{fmt(base['steady_state_active_share'], True)}**; variant A reaches "
      f"**{fmt(a['peak_active_share'], True)}** / **{fmt(a['steady_state_active_share'], True)}**; "
      f"variant B reaches **{fmt(b['peak_active_share'], True)}** / "
      f"**{fmt(b['steady_state_active_share'], True)}**.\n")

    w("### Churn — why people stopped\n")
    for lever in LEVERS:
        r = results[(SIZES[0], lever)]
        w(f"- **{LEVER_NAME[lever]}**: " + (", ".join(
            f"{k} ×{v:,}" for k, v in list(r["churn_reasons"].items())[:4]) or "—"))
    w("")

    # ── verbatim whys
    w("## 4. Two verbatim persona `why`s per touch type\n")
    for kind in sorted(raw.get("whys", {})):
        if kind == "signin":
            continue
        rows = raw["whys"][kind]
        acted = [r for r in rows if r.get("active_action")]
        ignored = [r for r in rows if not r.get("active_action")]
        w(f"**`{kind}`**\n")
        for tag, pick in (("acted", acted[:1]), ("did not act", ignored[:1])):
            for r in pick:
                w(f"- *{tag}* — `{r['persona']}` ({r['history']}): “{r['why']}”"
                  + (f"  · friction: *{r['friction']}*" if r.get("friction") else ""))
        w("")

    # ── interaction
    w("## 5. The interaction with the knowledge agent\n")
    if convs:
        told = sum(1 for c in convs if c["opened_by_telling"])
        val = [c for c in convs if c["got_value"]]
        w(f"- conversations run against the REAL agent: **{len(convs)}**")
        w(f"- opened by TELLING (the preset rule) rather than asking: **{told}/{len(convs)}**")
        w(f"- reached value at all: **{len(val)}/{len(convs)}**")
        acts = [x for c in convs for x in c["asked_actions"]]
        w(f"- actions the person asked for: {acts or 'none'}")
        w("")
        w("Abandonment, verbatim:")
        for c in convs:
            if c.get("abandon_why"):
                w(f"- `{c['persona']}`: “{c['abandon_why']}”")
    else:
        w("_not run in this revolution_")
    w("")

    w("## 6. What v0 does not model\n")
    w("Calendar reality (holidays, timezones, meetings people skip); the terminal UI beyond the "
      "click; IT provisioning and the tenant admitting the bot; anybody telling a colleague out "
      "loud; transcription quality. Quality enters only through what the mails actually say. "
      "The organizer's invite is admitted as a direct fact rather than a parsed ICS — the "
      "inbound mailbox double landed on a sibling branch during this run and feeds the "
      "founder's lane, not the sim's. The fixture is a DNA/ASWF working session, not a dailies "
      "review: no dailies transcript exists yet, and the personas judge CONTENT, so this is the "
      "single biggest gap between this measurement and SPI's real pilot.\n")

    json.dump({f"{n}|{lv}": {k: v for k, v in r.items() if k != "curve"}
               for (n, lv), r in results.items()},
              open(f"{RUN}/results.json", "w"), indent=1)
    text = "\n".join(out)
    open(f"{RUN}/REPORT.md", "w").write(text)
    print(text)


if __name__ == "__main__":
    sys.exit(main())
