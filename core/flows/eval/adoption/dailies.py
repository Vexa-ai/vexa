"""Synthetic DAILIES fixtures — the meeting the production cohort is actually in.

The pilot is coordinators and production managers "using it as their main tool", and the tool
is the Dailies Notes Assistant. The recorded corpus holds none: every DNA fixture is an
ASWF/TSC working session, which the insider cohort attends and the production cohort does not.
Revolution 1 measured production personas against a governance meeting they were never in, and
they ignored it for that reason — the instrument, not the product.

So these are generated: one dailies review per show per simulated day, written by Haiku FROM THE
ORG (its shows, departments, people and their roles), in the exact shape the DNA fixtures use so
the same replay path seeds them. Every one carries `synthetic: true` in its meeting meta and
lives under `~/dna-fixtures/synthetic/`, which is private and never enters a vexa checkout.

What they are NOT: evidence about SPI. They are a plausible dailies-shaped meeting, and any
number measured on them is a number about the product's behaviour on dailies-shaped input.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import cohorts
import judge

OUT = Path(os.environ.get("SIM_SYNTH_DIR", os.path.expanduser("~/dna-fixtures/synthetic")))
DEPT_WORK = {
    "Animation": ["blocking pass", "spline pass", "arcs on the hero turn", "contact on the landing",
                  "weight in the shoulders", "lip sync on the dialogue beat"],
    "Lighting": ["key on the practical", "bounce off the wet floor", "rim separation",
                 "dome intensity", "shadow density under the arch"],
    "FX": ["debris sim", "cloth pass on the cape", "smoke density", "water splash timing",
           "collision on the collapse"],
    "Compositing": ["edge blend on the plate", "grain match", "roto on the foreground",
                    "despill on the greenscreen", "depth haze"],
    "Layout": ["camera push timing", "staging of the crowd", "lens choice on the wide",
               "parallax on the background"],
    "Character/Modeling": ["silhouette on the hero", "topology at the elbow", "displacement on the hide",
                           "groom density"],
}

PROMPT = """Write the transcript of a real VFX dailies review at a feature animation studio.

THE ROOM — use these exact names as the speakers, nobody else:
{roster}

THE MEETING: {show}, {dept} dailies, {date}. {n_shots} shots under review this session.
Shot codes look like {seq}_0{a}0, {seq}_0{b}0, {seq}_0{c}0.

HOW DAILIES ACTUALLY GO — the coordinator drives the session and keeps time; the supervisor
gives the notes; artists answer for their own shots and say what they will change. Real
material: {work}. Shots get APPROVED, get NOTES, or go to RETAKE. There is at least one
scheduling pressure (a client review, a deadline, a handover) and at least one thing that is
deferred because someone is missing. People interrupt, agree in half-sentences, and use shot
codes constantly.

Write {n_lines} lines of speech. Output ONLY lines in this exact format, nothing else:

SPEAKER NAME | what they say

No preamble, no headings, no stage directions, no blank lines."""


def pace(lines: list, rng) -> list:
    """Turn speaker/text lines into timed segments that occupy a REAL dailies duration.

    Timing off speech length alone rendered a 100-line review as 7.8 minutes. Dailies are mostly
    silence: a shot plays (often two or three times), everyone watches, then somebody speaks.
    A duration-derived number taken off the un-paced version is wrong in the same way a
    transcript with no gaps is wrong, so this is not cosmetic.

    Targets the 20-40 minute band the DNA check-in describes ("their review runs 30 min"), by
    putting a playback pause between shot discussions rather than spreading time evenly.
    """
    out, t = [], float(rng.randint(20, 60))
    for i, ln in enumerate(lines):
        text = ln["text"]
        dur = max(1.4, min(14.0, len(text) / 15.0))
        out.append({"t": round(t, 2), "end": round(t + dur, 2),
                    "speaker": ln["speaker"], "text": text})
        t += dur + rng.uniform(0.4, 3.0)
        if i and i % rng.randint(4, 7) == 0:
            t += rng.uniform(15.0, 45.0)        # the shot plays again, twice, three times
    return out


def retime(path, seed: int = 0) -> float:
    """Re-pace a fixture already on disk — the generation is the expensive part, the clock is not."""
    import random as _r
    d = json.loads(Path(path).read_text())
    d["segments"] = pace([{"speaker": s["speaker"], "text": s["text"]} for s in d["segments"]],
                         _r.Random(str(path) + str(seed)))
    Path(path).write_text(json.dumps(d, indent=1))
    return round(d["segments"][-1]["end"] / 60.0, 1)


def _roster(org, show: str, dept: str, rng) -> list:
    team = [p for p in org.people if p.dept == show and p.team.endswith("/ " + dept)]
    office = [p for p in org.people if p.dept == show and "Production Office" in p.team]
    sup = [p for p in team if p.role == "supervisor"][:1]
    artists = [p for p in team if p.role == "artist"]
    coord = [p for p in office if p.role == "coordinator"][:1]
    pm = [p for p in office if p.role == "production_manager"][:1]
    picked = coord + sup + rng.sample(artists, min(4, len(artists))) + pm
    return picked or team[:4]


def generate(org, show: str, dept: str, date: str, seed: int = 0) -> dict | None:
    rng = random.Random(f"{show}{dept}{date}{seed}")
    people = _roster(org, show, dept, rng)
    if len(people) < 3:
        return None
    seq = f"{dept[:2].upper()}{rng.randint(10,99)}"
    prompt = PROMPT.format(
        roster="\n".join(f"- {p.name} — {p.role.replace('_',' ')}" for p in people),
        show=show, dept=dept, date=date, n_shots=rng.randint(3, 6), seq=seq,
        a=rng.randint(1, 4), b=rng.randint(5, 7), c=rng.randint(8, 9),
        work=", ".join(rng.sample(DEPT_WORK.get(dept, ["the shot"]),
                                  min(3, len(DEPT_WORK.get(dept, ["x"]))))),
        n_lines=rng.randint(110, 170))
    judge.ensure_cfg()
    names = {p.name for p in people}
    raw = ""
    for _attempt in range(3):
        raw = judge._ask(prompt, timeout=300)
        if raw and raw.count("|") >= 25:
            break                                        # a malformed first answer is common
    segs = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-•* ")
        if "|" not in line:
            continue
        who, _, text = line.partition("|")
        who, text = who.strip(), text.strip()
        if not text:
            continue
        if who not in names:                      # keep the roster honest
            match = [n for n in names if n.split()[0] == who.split()[0]] if who else []
            if not match:
                continue
            who = match[0]
        segs.append({"speaker": who, "text": text})
    if len(segs) < 25:
        return None
    segs = pace(segs, rng)
    return {
        "meeting": {
            "title": f"{show} {dept} dailies {date}",
            "platform": "meet",
            "native_meeting_id": re.sub(r"\W", "", f"{show}{dept}")[:12].lower() + date.replace("-", ""),
            "occurrence": date,
            "exact": "",
            "source": "generated",
            "synthetic": True,
            "cohort": cohorts.PRODUCTION,
            "show": show, "department": dept,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "participants": sorted(names),
        },
        "segments": segs,
    }


def main():
    import org as O
    import personas as P
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    o = O.build("spi", size)
    P.assign(o)
    shows = sorted({p.dept for p in o.people if p.dept.startswith("Show ")})
    depts = ["Animation", "Lighting", "Compositing"]
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    base = time.time()
    for d in range(n_days):
        date = time.strftime("%Y-%m-%d", time.gmtime(base + d * 86400))
        for i, show in enumerate(shows[:3]):            # one per show per day
            dept = depts[(d + i) % len(depts)]
            fx = generate(o, show, dept, date, seed=d)
            if not fx:
                print(f"  skip {show} {dept} {date} (generation too short)")
                continue
            name = f"{date}-{re.sub(r'[^a-z0-9]', '', show.lower())}-{dept.lower()}.transcript.json"
            (OUT / name).write_text(json.dumps(fx, indent=1))
            made.append((name, len(fx["segments"]),
                         round(fx["segments"][-1]["end"] / 60.0, 1)))
            print(f"  {name}  {len(fx['segments'])} segs  {made[-1][2]} min", flush=True)
    print(f"\n{len(made)} synthetic dailies fixtures in {OUT}")


if __name__ == "__main__":
    main()
