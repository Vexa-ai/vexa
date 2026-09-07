"""The org generator — people, units, and the MEETING GRAPH they live in.

The atom of intra-company growth is a calendar invite, not a signup (PRD §16): one person
invites the mailbox, every attendee is exposed. So the object this file builds is not an org
chart, it is the *graph of who sits in a room with whom, how often, and who owns the invite*.
Everything the simulator does afterwards walks that graph.

Generic in size, structure and cadence; a PROFILE supplies all three. Two profiles ship:

  spi   Sony Pictures Imageworks — the target org (founder 2026-09-02: "take SPI as a target
        … as insiders to DNA, as research target, to be native to the fixtures"). Working size
        1,300. NOTE THE ASSUMPTION: public reporting puts SPI at ~700 *production* staff at
        peak; Twenty's 5,000 is the Sony Pictures umbrella. 1,300 is the founder's working
        number and is used as given — it is not a headcount claim.
  bank  a 1,300-person central bank — the first profile written, kept as a second profile so
        a lever's sign can be checked against a different meeting graph.

Seeded: `build(...)` is a pure function of (profile, headcount, seed). Same inputs, same org.
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field

HEADCOUNT = 1300


@dataclass
class Person:
    pid: int
    name: str
    email: str
    dept: str           # show or studio function
    team: str
    role: str           # the profile's own role vocabulary
    persona: str = ""   # filled by personas.assign()


@dataclass
class Meeting:
    mid: str
    kind: str
    title: str
    organizer: int
    attendees: list[int]
    per_week: float          # occurrences per week (0.5 = fortnightly)
    external: bool = False   # out-of-domain guests -> the domain allow-list never mails these


@dataclass
class Org:
    people: list[Person]
    meetings: list[Meeting]
    teams: dict[str, list[int]] = field(default_factory=dict)
    profile: str = ""

    def organizes(self) -> dict[int, list[str]]:
        d: dict[int, list[str]] = {p.pid: [] for p in self.people}
        for m in self.meetings:
            d[m.organizer].append(m.mid)
        return d

    def attends(self) -> dict[int, list[str]]:
        d: dict[int, list[str]] = {p.pid: [] for p in self.people}
        for m in self.meetings:
            for a in m.attendees:
                d[a].append(m.mid)
        return d

    def by_pid(self) -> dict[int, Person]:
        return {p.pid: p for p in self.people}

    def to_json(self) -> str:
        return json.dumps({"profile": self.profile,
                           "people": [asdict(p) for p in self.people],
                           "meetings": [asdict(m) for m in self.meetings],
                           "teams": self.teams}, indent=1)


# ── names ────────────────────────────────────────────────────────────────────────────────────
_FIRST = {
    "spi": ["Cameron", "Priya", "Marcus", "Yuki", "Elena", "Tomas", "Aisha", "Dev", "Ruth",
            "Kenji", "Sofia", "Andre", "Mei", "Liam", "Nadia", "Oscar", "Farah", "Jonas",
            "Ravi", "Claire", "Ben", "Ingrid", "Hugo", "Talia", "Sam", "Noor", "Victor",
            "Hana", "Diego", "Maya", "Leo", "Zara", "Owen", "Iris"],
    "bank": ["Marvin", "Tobias", "Anna", "Lukas", "Sophie", "Felix", "Julia", "Stefan",
             "Katrin", "Michael", "Elena", "Christoph", "Barbara", "Andreas", "Petra",
             "Martin", "Claudia", "Thomas", "Sabine", "Georg", "Ines", "Peter", "Nina",
             "Wolfgang", "Eva", "Johannes", "Marlene", "David", "Theresa", "Simon"],
}
_LAST = {
    "spi": ["Tran", "Okafor", "Lindqvist", "Nakamura", "Rossi", "Duarte", "Khan", "Mehta",
            "Bell", "Sato", "Alvarez", "Dubois", "Chen", "Murphy", "Haddad", "Novak",
            "Iyer", "Beaulieu", "Kim", "Santos", "Wright", "Berg", "Moreau", "Levi",
            "Park", "Costa", "Wallace", "Ito", "Silva", "Grant"],
    "bank": ["Huber", "Gruber", "Wagner", "Muller", "Pichler", "Steiner", "Moser", "Mayer",
             "Hofer", "Leitner", "Berger", "Fuchs", "Eder", "Fischer", "Schmid", "Winkler",
             "Weber", "Schwarz", "Maier", "Schneider", "Reiter", "Mayr", "Wimmer", "Egger",
             "Brunner", "Lang", "Auer", "Binder", "Lechner", "Wolf"],
}


def _names(rng: random.Random, n: int, pool: str) -> list[str]:
    first, last = _FIRST[pool], _LAST[pool]
    out, seen = [], set()
    while len(out) < n:
        nm = f"{rng.choice(first)} {rng.choice(last)}"
        k, i = nm, 1
        while k in seen:
            i += 1
            k = f"{nm} {i}"
        seen.add(k)
        out.append(k)
    return out


# ── SPI ──────────────────────────────────────────────────────────────────────────────────────
# Grounded in the 2026-08-18 DNA dev check-in (Cottalango Leon, SPI): the pilot is "actual
# coordinators and production managers using it as their main tool", 3-5 of them, one show
# under NDA; dailies review "runs 30 min" and coordinators have "a couple of minutes to do
# everything" before the next; the big reviews carry "hundreds of people" and run "to three
# hours". The DNA product is the *Dailies Notes Assistant* — so DAILIES is the dominant
# recurring meeting, per show, per department, daily.
SPI_DEPTS = ["Layout", "Animation", "Lighting", "FX", "Compositing", "Character/Modeling"]
SPI_SHOW_MIX = 0.78        # share of headcount on shows; the rest is studio function
SPI_STUDIO = [             # name, weight, kind
    ("Pipeline & Engineering", 34, "eng"),
    ("Production Management", 16, "prodman"),
    ("Studio Technology", 12, "eng"),
    ("Editorial", 10, "post"),
    ("Studio Executive", 4, "exec"),
    ("HR & Recruiting", 8, "staff"),
    ("Finance & Legal", 8, "staff"),
    ("IT & Facilities", 8, "staff"),
]


def _build_spi(rng, headcount, org_people, teams, add):
    """Shows, each with coordinators / production managers / supervisors / department artists;
    plus the studio functions. Returns dept_meta."""
    n_show_people = round(headcount * SPI_SHOW_MIX)
    n_shows = max(2, round(n_show_people / 160))     # ~160 crew per show at 1.3k -> 6 shows
    show_names = [f"Show {chr(65+i)}" for i in range(n_shows)]
    per_show = n_show_people // n_shows
    pid = 0
    dept_meta = {}

    for sname in show_names:
        dept_meta[sname] = ("show", False)
        # the show's production office: managers + coordinators, the pilot users
        office = []
        n_pm = max(1, round(per_show * 0.02))
        n_coord = max(2, round(per_show * 0.05))
        for _ in range(n_pm):
            org_people.append((pid, sname, f"{sname} / Production Office", "production_manager"))
            office.append(pid); pid += 1
        for _ in range(n_coord):
            org_people.append((pid, sname, f"{sname} / Production Office", "coordinator"))
            office.append(pid); pid += 1
        teams[f"{sname} / Production Office"] = list(office)
        managers = office[:n_pm]
        coords = office[n_pm:]

        # the departments on this show: a supervisor + artists
        art_budget = per_show - len(office)
        dept_units: dict[str, list[int]] = {}
        for i, d in enumerate(SPI_DEPTS):
            size = max(3, art_budget // len(SPI_DEPTS) + (1 if i < art_budget % len(SPI_DEPTS) else 0))
            tname = f"{sname} / {d}"
            members = []
            org_people.append((pid, sname, tname, "supervisor"))
            members.append(pid); pid += 1
            for _ in range(size - 1):
                org_people.append((pid, sname, tname, "artist"))
                members.append(pid); pid += 1
            teams[tname] = members
            dept_units[d] = members
        _SPI_SHOWS.append((sname, managers, coords, dept_units))

    # studio functions
    rest = headcount - pid
    tw = sum(w for _, w, _ in SPI_STUDIO)
    for fname, w, kind in SPI_STUDIO:
        dept_meta[fname] = (kind, kind in ("exec", "staff"))
        size = max(2, round(rest * w / tw))
        remaining = size
        t = 0
        while remaining > 0:
            tsize = min(remaining, rng.randint(5, 11))
            if remaining - tsize in (1, 2, 3):
                tsize = remaining
            t += 1
            tname = f"{fname} / Team {t}"
            members = []
            for i in range(tsize):
                role = ("lead" if i == 0 else
                        {"eng": "engineer", "prodman": "production_manager", "post": "artist",
                         "exec": "exec", "staff": "staff"}[kind])
                org_people.append((pid, fname, tname, role))
                members.append(pid); pid += 1
            teams[tname] = members
            remaining -= tsize
    return dept_meta


_SPI_SHOWS: list = []


def _spi_meetings(rng, org: "Org", add, teams):
    """DAILIES first — daily, per show, per department. Then the production meeting, the
    supervisor sync, 1:1s, the pipeline dev check-in, and the cross-studio TSC (external)."""
    for sname, managers, coords, dept_units in _SPI_SHOWS:
        office = teams[f"{sname} / Production Office"]
        lead_pm = managers[0]
        for i, (d, members) in enumerate(dept_units.items()):
            # the coordinator running that department's dailies owns the invite
            coord = coords[i % len(coords)]
            sup = members[0]
            add("dailies", f"{sname} {d} dailies", coord,
                members + [coord, sup, lead_pm], 5.0)
        # the show production meeting — office + every supervisor, weekly
        sups = [m[0] for m in dept_units.values()]
        add("production_meeting", f"{sname} production meeting", lead_pm,
            office + sups, 1.0)
        # the big review: "hundreds of people … to three hours" — fortnightly, whole show
        whole = office + [p for ms in dept_units.values() for p in ms]
        add("show_review", f"{sname} show review", lead_pm, whole, 0.5)
        # 1:1s inside the production office
        for c in coords:
            add("one_on_one", f"1:1 {sname} production", lead_pm, [lead_pm, c], 0.5)
        # supervisor 1:1s with their department
        for d, members in dept_units.items():
            sup = members[0]
            for a in members[1:]:
                if rng.random() < 0.45:
                    add("one_on_one", f"1:1 {sname} {d}", sup, [sup, a], 0.5)

    by_dept: dict[str, list[Person]] = {}
    for p in org.people:
        by_dept.setdefault(p.dept, []).append(p)

    for fname, _w, kind in SPI_STUDIO:
        members = by_dept.get(fname, [])
        if not members:
            continue
        leads = [p.pid for p in members if p.role == "lead"] or [members[0].pid]
        for tname, mem in [(t, m) for t, m in teams.items() if t.startswith(fname + " /")]:
            add("team_weekly", f"{tname} weekly", mem[0], mem, 1.0)
            if kind == "eng":
                add("dev_checkin", f"{tname} dev check-in", mem[0], mem, 2.0)
        if len(leads) > 1:
            add("dept_leadership", f"{fname} leadership", leads[0], leads, 1.0)

    execs = [p.pid for p in org.people if p.role == "exec"]
    if len(execs) > 1:
        add("exec_staff", "Studio leadership", execs[0], execs, 1.0)

    # cross-show: pipeline engineers sit in show departments' meetings — how adoption escapes
    eng = [p.pid for p in org.people
           if p.role in ("engineer", "lead")
           and ("Engineering" in p.dept or "Technology" in p.dept)]
    shows_all = [m for m in org.meetings if m.kind in ("production_meeting", "dev_checkin")]
    for m in shows_all:
        if eng and rng.random() < 0.30:
            m.attendees = sorted(set(m.attendees + rng.sample(eng, min(2, len(eng)))))

    # the TSC / ASWF working groups — external by construction, never mailed by the product
    pipeline = [p.pid for p in org.people if p.dept in ("Pipeline & Engineering",
                                                        "Studio Technology")]
    if pipeline:
        for i in range(3):
            host = rng.choice(pipeline)
            add("tsc", f"ASWF working group {i+1}", host,
                [host] + rng.sample(pipeline, min(4, len(pipeline))), 0.5, external=True)
    # vendor / client reviews — also external
    prod = [p.pid for p in org.people if p.role in ("production_manager", "exec")]
    for i in range(max(1, round(len(prod) * 0.4))):
        host = rng.choice(prod)
        add("client_review", f"Client review {i+1}", host,
            [host] + rng.sample(prod, min(3, len(prod))), 0.5, external=True)


# ── bank (the second profile, unchanged in shape from the first worker's draft) ──────────────
BANK_DEPTS = [
    ("Retail Operations", 190, False, False), ("Payments", 140, True, True),
    ("IT Infrastructure", 150, True, False), ("Data & Analytics", 90, True, False),
    ("Risk Management", 130, False, False), ("Compliance", 110, False, True),
    ("Treasury", 85, False, True), ("Internal Audit", 70, False, False),
    ("Legal", 55, False, True), ("Human Resources", 75, False, True),
    ("Corporate Banking", 120, False, True), ("Communications", 45, False, True),
    ("Statistics", 40, False, False),
]


def _build_bank(rng, headcount, org_people, teams, add):
    total_w = sum(d[1] for d in BANK_DEPTS)
    dept_meta = {}
    pid = 0
    for dname, w, agile, external in BANK_DEPTS:
        dept_meta[dname] = (agile, external)
        size = round(headcount * w / total_w)
        remaining, t = size, 0
        while remaining > 0:
            tsize = min(remaining, rng.randint(4, 12))
            if remaining - tsize in (1, 2, 3):
                tsize = remaining
            t += 1
            tname = f"{dname} / Team {t}"
            members = []
            for i in range(tsize):
                role = "manager" if i == 0 else rng.choices(["ic", "assistant"],
                                                            weights=[0.92, 0.08])[0]
                org_people.append((pid, dname, tname, role))
                members.append(pid); pid += 1
            teams[tname] = members
            remaining -= tsize
    return dept_meta


def _bank_meetings(rng, org, add, teams, dept_meta):
    by_dept: dict[str, list[Person]] = {}
    for p in org.people:
        by_dept.setdefault(p.dept, []).append(p)
    for tname, members in teams.items():
        dname = tname.split(" / ")[0]
        agile, _ = dept_meta[dname]
        lead = members[0]
        add("team_weekly", f"{tname} weekly", lead, members, 1.0)
        if agile:
            add("standup", f"{tname} standup", lead, members, 4.0)
        for m in members[1:]:
            add("one_on_one", f"1:1 {tname}", lead, [lead, m], 0.5)
    for dname, members in by_dept.items():
        leads = [p.pid for p in members if p.role in ("manager", "exec")]
        if len(leads) > 1:
            add("dept_leadership", f"{dname} leadership", leads[0], leads, 1.0)
    execs = [p.pid for p in org.people if p.role == "exec"]
    if len(execs) > 1:
        add("exec_staff", "Board staff meeting", execs[0], execs, 1.0)
    team_names = list(teams)
    for i in range(round(len(org.people) / 9)):
        picked = rng.sample(team_names, rng.randint(2, 4))
        att: list[int] = []
        for tn in picked:
            att += rng.sample(teams[tn], min(len(teams[tn]), rng.randint(1, 3)))
        if len(att) >= 3:
            add("project", f"Project {i+1} sync", rng.choice(att), att, rng.choice([1.0, 1.0, 0.5]))
    ext_pool = [p.pid for p in org.people if dept_meta[p.dept][1]]
    for i in range(round(len(ext_pool) * 0.25)):
        host = rng.choice(ext_pool)
        peers = [p.pid for p in org.people if p.dept == org.by_pid()[host].dept]
        add("external", f"External review {i+1}", host,
            [host] + rng.sample(peers, min(3, len(peers))), 0.3, external=True)


DOMAIN = {"spi": "imageworks.example", "bank": "bank.example"}


def build(profile: str = "spi", headcount: int = HEADCOUNT, seed: int = 7) -> Org:
    global _SPI_SHOWS
    _SPI_SHOWS = []
    rng = random.Random(seed)
    raw: list[tuple] = []
    teams: dict[str, list[int]] = {}
    meetings: list[Meeting] = []
    mid = [0]

    def add(kind, title, organizer, attendees, per_week, external=False):
        mid[0] += 1
        meetings.append(Meeting(f"m{mid[0]}", kind, title, organizer,
                                sorted(set(attendees)), per_week, external))

    if profile == "spi":
        dept_meta = _build_spi(rng, headcount, raw, teams, add)
    elif profile == "bank":
        dept_meta = _build_bank(rng, headcount, raw, teams, add)
    else:
        raise ValueError(f"unknown profile {profile!r}")

    names = _names(rng, len(raw) + 5, profile)
    dom = DOMAIN[profile]
    people = [Person(pid, names[pid], f"p{pid}@{dom}", dept, team, role)
              for pid, dept, team, role in raw]
    org = Org(people, meetings, teams, profile)
    if profile == "spi":
        _spi_meetings(rng, org, add, teams)
    else:
        _bank_meetings(rng, org, add, teams, dept_meta)
    org.meetings = meetings
    return org


def stats(org: Org) -> dict:
    per_person = {p.pid: 0.0 for p in org.people}
    for m in org.meetings:
        for a in m.attendees:
            per_person[a] = per_person.get(a, 0) + m.per_week
    by_role: dict[str, list[float]] = {}
    for p in org.people:
        by_role.setdefault(p.role, []).append(per_person[p.pid])
    by_kind: dict[str, float] = {}
    for m in org.meetings:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + m.per_week
    return {
        "profile": org.profile,
        "people": len(org.people),
        "teams": len(org.teams),
        "meeting_series": len(org.meetings),
        "meetings_per_week_total": round(sum(m.per_week for m in org.meetings), 1),
        "avg_meetings_per_person_per_week": round(sum(per_person.values()) / len(org.people), 2),
        "seats_per_week": round(sum(m.per_week * len(m.attendees) for m in org.meetings), 0),
        "by_role": {r: {"n": len(v), "avg_meetings_wk": round(sum(v) / len(v), 2)}
                    for r, v in sorted(by_role.items())},
        "by_kind_per_week": {k: round(v, 1) for k, v in sorted(by_kind.items(),
                                                               key=lambda x: -x[1])},
        "people_with_no_meetings": sum(1 for v in per_person.values() if v == 0),
        "organizers": len({m.organizer for m in org.meetings}),
        "external_series": sum(1 for m in org.meetings if m.external),
    }


if __name__ == "__main__":
    import sys
    prof = sys.argv[1] if len(sys.argv) > 1 else "spi"
    hc = int(sys.argv[2]) if len(sys.argv) > 2 else HEADCOUNT
    print(json.dumps(stats(build(prof, hc)), indent=1))
