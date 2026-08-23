"""The zero-domain step set: same names and shapes as the real adapters, canned behavior a test
controls. `FakeWorld` records every effect so the fixtures/storm can assert exactly-once."""
from __future__ import annotations

from dataclasses import dataclass, field

from flows import Block, Done, EventType, Registry, StepCtx, StepError, Wait

INVITE_RECEIVED = EventType("invite.received")
MEETING_COMPLETED = EventType("meeting.completed")


@dataclass
class FakeWorld:
    """The observable outside world: what got done, exactly-once-ness is asserted on these."""
    meetings_created: list[str] = field(default_factory=list)
    bots_dispatched: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    emails: list[tuple[str, str]] = field(default_factory=list)   # (recipient, artifact)
    meeting_state: dict = field(default_factory=dict)             # meeting -> completed? final?
    workspaces_ready: set = field(default_factory=set)
    research: list = field(default_factory=list)
    followups: list = field(default_factory=list)
    admit_fn: object = None                               # wired by the rig for fact-emitting steps
    # fault injection dials
    fail_next: dict[str, int] = field(default_factory=dict)       # step -> remaining failures
    fail_after_effect: set = field(default_factory=set)           # step crashes AFTER doing the effect

    def maybe_fault(self, step: str, *, before: bool) -> None:
        if before and self.fail_next.get(step, 0) > 0:
            self.fail_next[step] -= 1
            raise StepError(f"injected fault before {step}")

    def crash_after(self, step: str) -> None:
        if step in self.fail_after_effect:
            self.fail_after_effect.discard(step)
            raise RuntimeError(f"injected crash AFTER effect in {step}")


def build_registry(world: FakeWorld) -> Registry:
    reg = Registry()

    @reg.step
    def create_meeting(ctx: StepCtx):
        world.maybe_fault("create_meeting", before=True)
        mid = ctx.refs["meeting"]
        if mid not in world.meetings_created:          # domain-side idempotent claim
            world.meetings_created.append(mid)
        world.crash_after("create_meeting")
        return Done({"meeting_id": mid, "start_time": ctx.refs.get("start_time", ctx.clock_now + 3600)})

    @reg.step
    def confirm_by_email(ctx: StepCtx):
        world.maybe_fault("confirm_by_email", before=True)
        world.emails.append((ctx.refs["inviter"], "confirm"))
        world.crash_after("confirm_by_email")
        return Done({"message_id": f"confirm-{ctx.reaction.reaction_id[:6]}"}, provider_ref="smtp")

    @reg.step
    def await_start(ctx: StepCtx):
        starts = ctx.prior["create_meeting"]["start_time"]
        if ctx.clock_now < starts:
            return Wait(until=starts)
        return Done({})

    @reg.step
    def dispatch_bot(ctx: StepCtx):
        world.maybe_fault("dispatch_bot", before=True)
        mid = ctx.refs["meeting"]
        if mid not in world.bots_dispatched:           # idempotent by effect check
            world.bots_dispatched.append(mid)
        world.crash_after("dispatch_bot")
        return Done({"workload": f"bot-{mid}"}, provider_ref=f"bot-{mid}")

    @reg.step
    def await_completion(ctx: StepCtx):
        m = world.meeting_state.get(ctx.refs["meeting"], {})
        if not (m.get("completed") and m.get("final")):
            return Wait(seconds=60)
        return Done({"transcript_ref": f"tr-{ctx.refs['meeting']}"})

    @reg.step
    def process_transcript(ctx: StepCtx):
        world.maybe_fault("process_transcript", before=True)
        return Done({"summary_ref": f"sum-{ctx.refs['meeting']}"})

    @reg.step
    def commit_summary(ctx: StepCtx):
        world.maybe_fault("commit_summary", before=True)
        sha = f"sha-{ctx.refs['meeting']}"
        if sha not in world.commits:                   # no competing commits, ever
            world.commits.append(sha)
        world.crash_after("commit_summary")
        return Done({"commit_sha": sha}, provider_ref=sha)

    @reg.step
    def email_participants(ctx: StepCtx):
        world.maybe_fault("email_participants", before=True)
        sha = ctx.prior["commit_summary"]["commit_sha"]     # physically after the commit
        inside = [p for p in ctx.refs.get("participants", []) if p.endswith("@bank.com")]
        for r in inside:
            if (r, sha) not in world.emails:           # per-recipient idempotency
                world.emails.append((r, sha))
        world.crash_after("email_participants")
        return Done({"sent": inside})

    @reg.step
    def notify_organizer(ctx: StepCtx):
        world.maybe_fault("notify_organizer", before=True)
        if (ctx.refs["inviter"], "scheduled") not in world.emails:
            world.emails.append((ctx.refs["inviter"], "scheduled"))
        world.crash_after("notify_organizer")
        return Done({})

    @reg.step
    def ensure_onboarding(ctx: StepCtx):
        """No personal workspace → EMIT the onboarding.needed fact (sub-flow composition)."""
        owner = ctx.refs["inviter"]
        if owner in world.workspaces_ready:
            return Done({"already": True})
        n = world.admit_fn(source_event_id=f"onb-{owner}", event_type="onboarding.needed",
                           subject_refs={"person": owner, **ctx.refs})
        return Done({"onboarding_started": n})

    @reg.step
    def research_person(ctx: StepCtx):
        world.maybe_fault("research_person", before=True)
        person = ctx.refs["person"]
        world.research.append(person)                     # name+company lookup happened
        return Done({"guess": {"name": person.split("@")[0].title(),
                               "company": person.split("@")[1].split(".")[0].title()}})

    @reg.step
    def ask_one_question(ctx: StepCtx):
        g = ctx.prior["research_person"]["guess"]
        world.emails.append((ctx.refs["person"], "onboarding-question"))
        return Done({"asked": f"You are {g['name']} at {g['company']} — correct? What's your role?"})

    @reg.step
    def await_human_reply(ctx: StepCtx):
        # Block until the human replies (a resume signal). The reconciler's blocked_deadline is
        # the follow-up cadence: on escalation the OUTER loop re-asks instead of failing —
        # modeled here as a bounded Block; follow-ups are receipts of ask_one_question re-sends.
        return Block("awaiting human reply to the onboarding question", deadline_s=172800)

    @reg.step
    def setup_personal_workspace(ctx: StepCtx):
        person = ctx.refs["person"]
        world.workspaces_ready.add(person)                # .scaffolded written
        world.emails.append((person, "workspace-ready"))
        return Done({"workspace": f"ws-{person}"})

    @reg.step
    def require_workspace(ctx: StepCtx):
        """THE QUEUE: processing waits for readiness; each wake sends a follow-up nudge."""
        owner = ctx.refs["inviter"]
        if owner in world.workspaces_ready:
            return Done({"ready": True})
        world.followups.append(owner)                     # "finish your setup to get your summary"
        world.emails.append((owner, "setup-nudge"))
        return Wait(seconds=3600)                         # re-check hourly; unbounded on purpose —
                                                          # the summary must not be lost, only late

    @reg.step
    def needs_approval(ctx: StepCtx):
        if not ctx.refs.get("approved"):
            return Block("awaiting owner approval", deadline_s=86400)
        return Done({"approved_by": ctx.refs.get("approver", "owner")})

    return reg
