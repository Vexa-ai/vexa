"""The `to_stream` step: the stream primitive's producer (`flows.stream`).

Registered by the production vocabulary and by any pack whose flows use it (the crm pack's
`card_due_alarms`), once per registry: `register` answers the step already bound."""
from __future__ import annotations

from flows import Done, StepCtx, StepError


def register(reg, db):
    if "to_stream" in reg.steps:
        return reg.steps["to_stream"]

    def to_stream(ctx: StepCtx):
        """Append the event that started this run to ONE recipient's stream, where it stays until the
        recipient acknowledges it (`POST /streams/{recipient}/ack`). The recipient is the flow's
        `recipient` param, else refs.owner. A keyed fire appends as the event that armed it.
        Reads: params.recipient · refs.owner · Effect: one stream item, once per (recipient, source
        event) · Result: {recipient, item_id, status}."""
        from flows import keyed, stream
        recipient = str(ctx.flow.param("recipient", "") if ctx.flow is not None else "").strip() \
            or str(ctx.refs.get("owner") or "").strip()
        if not recipient:
            raise StepError("to_stream needs a recipient: the flow's `recipient` param or refs.owner",
                            retryable=False)
        event_type = ctx.reaction.event_type
        if event_type == keyed.KEYED_EVENT and ctx.refs.get("_event_type"):
            event_type = str(ctx.refs["_event_type"])
        try:
            out = stream.append(db, ctx.clock_now, recipient=recipient,
                                source_event_id=ctx.reaction.source_event_id, event_type=event_type,
                                refs=ctx.refs, reaction_id=ctx.reaction_id)
        except stream.StreamError as e:
            raise StepError(str(e), retryable=False)
        return Done({"recipient": recipient, **out}, provider_ref=out.get("item_id"))

    return reg.step(to_stream)
