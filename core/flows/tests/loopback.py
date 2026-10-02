"""The LOOPBACK fixture — a real workflow round trip where the world answers back.

    invite fact ──▶ [invite_to_bot flow] create_meeting → confirm email → await_start → dispatch_bot
                                                                                │ (effect)
                       the world: bot joins, meeting runs, transcript finalizes │
                                                                                ▼
    webhook fact  ◀── the provider/webhook integration emits meeting.completed ─┘
        │
        └─▶ [post_meeting flow] process_transcript → commit_summary → email_participants

Two flows, chained ONLY by facts through the world — exactly the production decomposition. The
webhook is delivered 1..3 times (transport retries) to prove the loopback dedups like any fact."""
from __future__ import annotations

from dataclasses import dataclass, field

from flows import FakeClock, Registry, admit, escalate, reclaim, tick
from sqlite_double import SqliteDB
from flows_steps.fakes import FakeWorld, INVITE_RECEIVED, MEETING_COMPLETED, build_registry


@dataclass
class LoopbackWorld(FakeWorld):
    """FakeWorld that CLOSES THE LOOP: a dispatched bot transcribes for `duration_s`, then the
    webhook integration admits meeting.completed back into flows — with transport-level
    redelivery, like the real webhook queue."""
    duration_s: float = 1800.0
    webhook_redeliveries: int = 2
    _pending: dict = field(default_factory=dict)        # meeting -> {refs, done_at}
    _delivered: list = field(default_factory=list)

    def on_dispatch(self, meeting: str, refs: dict, now: float) -> None:
        self._pending[meeting] = {"refs": dict(refs), "done_at": now + self.duration_s}

    def pump(self, db, reg: Registry, clock) -> int:
        """The world advancing: finished meetings finalize + fire the webhook (multiple times)."""
        fired = 0
        for meeting, p in list(self._pending.items()):
            if clock.now() >= p["done_at"]:
                self.meeting_state[meeting] = {"completed": True, "final": True}
                for _ in range(1 + self.webhook_redeliveries):
                    admit(db, reg, clock, source_event_id=f"whk-{meeting}",
                          event_type=MEETING_COMPLETED.name, subject_refs=p["refs"])
                self._delivered.append(meeting)
                del self._pending[meeting]
                fired += 1
        return fired


def loopback_rig():
    world = LoopbackWorld()
    reg = build_registry(world)

    # dispatch_bot must tell the world so the world can answer back — wrap the fake step
    inner = reg.steps["dispatch_bot"]

    def dispatch_bot(ctx):
        out = inner(ctx)
        world.on_dispatch(ctx.refs["meeting"], ctx.refs, ctx.clock_now)
        return out
    reg.steps["dispatch_bot"] = dispatch_bot

    s = reg.steps
    reg.flow(name="invite_to_bot", version=1, on=INVITE_RECEIVED,
             steps=[s["create_meeting"], s["confirm_by_email"], s["await_start"], s["dispatch_bot"]])
    reg.flow(name="post_meeting", version=1, on=MEETING_COMPLETED,
             steps=[s["await_completion"], s["process_transcript"],
                    s["commit_summary"], s["email_participants"]])
    return SqliteDB(), reg, FakeClock(), world


def drain_with_world(db, reg, clock, world, *, max_ticks: int = 2000) -> None:
    """Like fixtures.drain, but the WORLD runs too: each idle moment pumps the loopback and
    jumps the clock to the earliest of (next reaction due, next world event)."""
    for _ in range(max_ticks):
        reclaim(db, clock)
        escalate(db, clock)
        world.pump(db, reg, clock)
        if tick(db, reg, clock):
            continue
        due = db.execute("SELECT MIN(next_run_at) FROM reaction "
                         "WHERE status IN ('admitted','retrying')")[0][0]
        world_next = min((p["done_at"] for p in world._pending.values()), default=None)
        candidates = [t for t in (due, world_next) if t is not None]
        if not candidates:
            return
        nxt = min(candidates)
        if nxt <= clock.now():
            continue
        clock._t = nxt
    raise AssertionError("loopback drain did not converge")
