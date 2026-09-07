"""The daily dynamics — the objective function.

The clock is DAILY. Each simulated day the meeting graph fires the meetings due that day; each
firing produces the touches the REAL product produces for that meeting (measured on the stack,
not invented here); each touch is a persona decision. The org is too large to put every touch
through Haiku, so the model is two-layer and says so:

  layer 1  SAMPLE — real identities, real flows, real mail text, one Haiku call per touch.
           Produces `rates.json`: per (persona × touch_kind × history-state) the share who
           opened, and of those, the share who took a UI ACTION.
  layer 2  EXTRAPOLATE — this file, walking the whole graph on those measured rates.

ACTIVE (founder, 2026-09-02): opened a Vexa mail AND took at least one UI action — clicked into
the terminal, sent a chat turn, replied to the mail, or invited the mailbox — within the
trailing 14 days. Delivered mail counts for nothing; an open alone is `reached`, not active.

T_full is reported as the day the ACTIVE share crosses a stated threshold, never as "everyone":
no org reaches 100%, and a number that can only be reached asymptotically is not a measurement.
The number is relative between revolutions. It is NEVER a forecast.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field

ACTIVE_WINDOW = 14          # days: the trailing window in which an action must have happened
CHURN_AFTER = 14            # days ignored in a row -> inactive
FULL_THRESHOLD = 0.80       # "full adoption" = this share of the reachable population active
MAIL_FATIGUE = 3            # mails/day above which the marginal one is much likelier ignored


HISTORY_BUCKETS = ["fresh", "one_ignore", "two_ignores"]


def bucket(consec_ignored: int) -> str:
    """The history state the SAMPLE actually measured, not a decay curve fitted to nothing."""
    return HISTORY_BUCKETS[min(consec_ignored, len(HISTORY_BUCKETS) - 1)]


@dataclass
class Rates:
    """Measured on the stack, keyed (persona, touch_kind, history) ->
    {open, act_given_open, invite, forward}.

    There is no `history_penalty` any more. The first version of this file aggregated the three
    measured history states together and then re-invented the same effect as an unbounded
    exponential — an asserted constant standing in front of a measured number, and one that
    drove every population to extinction (after ten ignored touches it put the open probability
    at 1%, so no lever could retain anybody). Consecutive ignores now select the bucket that was
    sampled."""
    table: dict
    fatigue_penalty: float = 0.65      # ASSUMED, not measured: the marginal mail in a busy day
    default: tuple = (0.35, 0.20, 0.02, 0.02)

    def get(self, persona: str, kind: str, hist: str = "fresh"):
        v = (self.table.get(f"{persona}|{kind}|{hist}")
             or self.table.get(f"{persona}|{kind}")
             or self.table.get(f"*|{kind}|{hist}")
             or self.table.get(f"*|{kind}"))
        if not v:
            return self.default
        return (v["open"], v["act_given_open"], v.get("invite", 0.0), v.get("forward", 0.0))

    @staticmethod
    def load(path: str) -> "Rates":
        d = json.load(open(path))
        return Rates(table=d.get("table_by_history") or d["table"],
                     fatigue_penalty=d.get("fatigue_penalty", 0.65),
                     default=tuple(d.get("default", (0.35, 0.20, 0.02, 0.02))))


@dataclass
class State:
    ever_active: set = field(default_factory=set)
    last_action_day: dict = field(default_factory=dict)   # pid -> day
    reached: set = field(default_factory=set)             # opened at least once
    consec_ignored: dict = field(default_factory=dict)
    invited: set = field(default_factory=set)             # pids whose meetings carry the mailbox
    became_active_day: dict = field(default_factory=dict)
    churn_reasons: dict = field(default_factory=dict)
    mails_today: dict = field(default_factory=dict)
    exposed: set = field(default_factory=set)     # saw the bot in a participant list (H2)
    seed_size: int = 0


def _touches_for(meeting, org_state, is_seeded, attendee_followup: str):
    """What the REAL product emits for one occurrence of one meeting. This is the product's
    behaviour as measured on the stack, expressed as a list of (pid, touch_kind).

      organizer has the mailbox invited  -> prepare (before), minutes (after)
      attendees                          -> NOTHING, unless the attendee follow-up is on
    External meetings are never mailed at all (the domain allow-list)."""
    if meeting.external or meeting.organizer not in is_seeded:
        return []
    out = [(meeting.organizer, "prepare"), (meeting.organizer, "minutes")]
    if attendee_followup != "off":
        kind = "attendee_shared" if attendee_followup == "shared" else "attendee_personal"
        for a in meeting.attendees:
            if a != meeting.organizer:
                out.append((a, kind))
    return out


SEED_STRATEGIES = {
    # (i) the pilot as it stands — Cottalango's "3-5 coordinators and production managers
    #     using it as their main tool", picked without regard to WHAT they organize.
    "pilot_random": "3-5 coordinators/PMs, chosen at random",
    # (ii) structure: one coordinator per SHOW. A show's dailies are organized by its
    #      production coordinator, and one coordinator organizes many recurring meetings, so
    #      touching one touches that show every day.
    "one_coordinator_per_show": "the dailies coordinator of every show",
    # (iii) the whole production office.
    "all_coordinators_and_pms": "every coordinator + production manager",
    # (iv) admin-driven, stage 1 of the alpha: the mailbox is on every recurring dailies from
    #      day 0, so no organizer has to be converted first for the dailies to be covered.
    "admin_all_dailies": "admin puts the mailbox on every recurring dailies",
}


def run(org, rates: Rates, days: int = 120, seed: int = 3,
        attendee_followup: str = "off", seeds_n: int = 3, seed_role=None,
        full_threshold: float = FULL_THRESHOLD,
        seed_strategy: str = "pilot_random",
        presence_lift: float = 0.0) -> dict:
    """One simulated adoption run. `attendee_followup` is the lever under test:
       off       the product as it stands on the line (organizer only) — THE NULL
       shared    one follow-up body to every inside-domain attendee (variant A)
       personal  a per-person block from the same single agent run (variant B)
    """
    rng = random.Random(seed)
    st = State()
    people = {p.pid: p for p in org.people}

    # WHO IS SEEDED ON DAY 0 — the reach bottleneck. Every size and cohort measured so far lost
    # 89-100% of its population here: not to a bad touch, but because nobody ever received one.
    # The seed is a PILOT-DESIGN choice, not a property of the product, so it is a parameter.
    people_by_pid = {p.pid: p for p in org.people}
    roles = seed_role or ("coordinator", "production_manager")
    if seed_strategy == "one_coordinator_per_show":
        # one per show, and specifically one who ORGANIZES that show's dailies
        by_show: dict = {}
        for m in org.meetings:
            if m.kind != "dailies":
                continue
            p = people_by_pid.get(m.organizer)
            if p is None:
                continue
            by_show.setdefault(p.dept, set()).add(m.organizer)
        seeded = {sorted(v)[0] for v in by_show.values() if v}
    elif seed_strategy == "all_coordinators_and_pms":
        seeded = {p.pid for p in org.people if p.role in ("coordinator", "production_manager")}
    elif seed_strategy == "admin_all_dailies":
        # the admin puts the mailbox on the MEETINGS, so every dailies is covered from day 0
        # whether or not its organizer has ever done anything. The organizers are marked invited
        # because the mailbox is on their meeting; they are not marked ACTIVE, because an admin
        # action is not their action — that is the whole distinction `active` exists to make.
        seeded = {m.organizer for m in org.meetings if m.kind == "dailies"}
    else:
        pool = [p.pid for p in org.people if p.role in roles]
        if not pool:
            pool = [m.organizer for m in org.meetings]
        seeded = set(rng.sample(pool, min(seeds_n, len(pool))))

    for pid in seeded:
        st.invited.add(pid)
        if seed_strategy != "admin_all_dailies":
            # a person who chose to use it starts active; a person whose admin acted does not
            st.ever_active.add(pid)
            st.last_action_day[pid] = 0
            st.became_active_day[pid] = 0
    st.seed_size = len(seeded)

    # who could EVER be reached: anyone in a non-external meeting. Nobody else has a path.
    reachable = {a for m in org.meetings if not m.external for a in m.attendees}

    curve = []
    crossed: dict = {}
    per_touch_counts: dict = {}

    for day in range(1, days + 1):
        st.mails_today = {}
        # which meeting series fire today — per_week/5 working days, Bernoulli per day
        firing = [m for m in org.meetings if rng.random() < min(1.0, m.per_week / 5.0)]
        touches = []
        for m in firing:
            if not m.external and m.organizer in st.invited:
                st.exposed.update(m.attendees)      # they saw the bot in the room today
            touches += _touches_for(m, st, st.invited, attendee_followup)
        rng.shuffle(touches)

        for pid, kind in touches:
            p = people.get(pid)
            if p is None:
                continue
            n_today = st.mails_today.get(pid, 0)
            st.mails_today[pid] = n_today + 1
            o, a, inv, fwd = rates.get(p.persona, kind,
                                       bucket(st.consec_ignored.get(pid, 0)))
            # H2 — PRESENCE. The bot sits in the participant list of that show's dailies every
            # day, so the name is familiar before any mail arrives. Modelled as a multiplier on
            # the decision, never as a channel of its own: presence exposes, it does not deliver.
            if presence_lift and pid in st.exposed:
                o = min(1.0, o * (1.0 + presence_lift))
                a = min(1.0, a * (1.0 + presence_lift))
            if n_today >= MAIL_FATIGUE:
                o *= rates.fatigue_penalty ** (n_today - MAIL_FATIGUE + 1)
            per_touch_counts[kind] = per_touch_counts.get(kind, 0) + 1

            if rng.random() >= o:
                st.consec_ignored[pid] = st.consec_ignored.get(pid, 0) + 1
                if st.mails_today[pid] > MAIL_FATIGUE:
                    st.churn_reasons["too many mails in a day"] = \
                        st.churn_reasons.get("too many mails in a day", 0) + 1
                continue
            st.reached.add(pid)
            if rng.random() >= a:                      # opened but did nothing = NOT active
                st.consec_ignored[pid] = st.consec_ignored.get(pid, 0) + 1
                st.churn_reasons["opened, nothing worth doing"] = \
                    st.churn_reasons.get("opened, nothing worth doing", 0) + 1
                continue
            st.consec_ignored[pid] = 0
            st.last_action_day[pid] = day
            if pid not in st.ever_active:
                st.ever_active.add(pid)
                st.became_active_day[pid] = day
            # the growth atom: an active person putting the mailbox on a meeting THEY own
            if pid not in st.invited and rng.random() < inv:
                st.invited.add(pid)
            if rng.random() < fwd:                     # a forward exposes a colleague's invite
                mates = [x for m in org.meetings if pid in m.attendees and not m.external
                         for x in m.attendees if x != pid]
                if mates:
                    other = rng.choice(mates)
                    if other not in st.invited and rng.random() < 0.35:
                        st.invited.add(other)

        active = {pid for pid, d in st.last_action_day.items() if day - d <= ACTIVE_WINDOW}
        share = len(active) / max(1, len(reachable))
        curve.append({"day": day, "active": len(active), "active_share": round(share, 4),
                      "ever_active": len(st.ever_active), "reached": len(st.reached),
                      "invited": len(st.invited)})
        for thr in (0.25, 0.50, 0.80):
            if thr not in crossed and share >= thr:
                crossed[thr] = day

    # retention: of those who BECAME active, the share still active 30 / 90 days later
    def retention(after: int) -> float | None:
        elig = [pid for pid, d0 in st.became_active_day.items() if d0 + after <= days]
        if not elig:
            return None
        still = [pid for pid in elig
                 if (d0 := st.became_active_day[pid]) is not None
                 and st.last_action_day.get(pid, -999) >= d0 + after - ACTIVE_WINDOW]
        return round(len(still) / len(elig), 4)

    tail = curve[-min(21, len(curve)):]
    steady = round(sum(c["active_share"] for c in tail) / len(tail), 4)
    return {
        "lever": attendee_followup,
        "seed_strategy": seed_strategy,
        "seed_size": st.seed_size,
        "seed_share": round(st.seed_size / max(1, len(org.people)), 5),
        "presence_lift": presence_lift,
        "exposed": len(st.exposed),
        "headcount": len(org.people),
        "reachable": len(reachable),
        "days": days,
        "t_full_days": crossed.get(0.80),
        "t_25_days": crossed.get(0.25),
        "t_50_days": crossed.get(0.50),
        "full_threshold": full_threshold,
        "peak_active_share": round(max(c["active_share"] for c in curve), 4),
        "steady_state_active_share": steady,
        "retention_30": retention(30),
        "retention_90": retention(90),
        "ever_active": len(st.ever_active),
        "ever_reached": len(st.reached),
        "invited_mailbox": len(st.invited),
        "touches_by_kind": per_touch_counts,
        "churn_reasons": dict(sorted(st.churn_reasons.items(), key=lambda x: -x[1])),
        "curve": curve,
    }


def summarize(r: dict) -> str:
    def d(v):
        return str(v) if v else ">" + str(r["days"])
    return (f"{r['lever']:<10} n={r['headcount']:<7} "
            f"T25={d(r['t_25_days']):<5} T50={d(r['t_50_days']):<5} "
            f"T80={d(r['t_full_days']):<5} "
            f"peak={r['peak_active_share']*100:5.1f}%  "
            f"steady={r['steady_state_active_share']*100:5.1f}%  "
            f"ret30={r['retention_30']}  ret90={r['retention_90']}")


if __name__ == "__main__":
    import sys

    import org as O
    import personas as P
    rates = Rates.load(sys.argv[1] if len(sys.argv) > 1 else "rates.json")
    prof = sys.argv[2] if len(sys.argv) > 2 else "spi"
    for n in [int(x) for x in (sys.argv[3].split(",") if len(sys.argv) > 3 else ["2000"])]:
        o = O.build(prof, n)
        P.assign(o)
        for lever in ("off", "shared", "personal"):
            print(summarize(run(o, rates, days=120, attendee_followup=lever)))
