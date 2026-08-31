# flows.v1 — the flows control-plane REST surface — UNSEALED (stub)

The MCP/agent → flows-api boundary: submit a flow, list flows + the step vocabulary, activate/retire
a version, list reactions, signal one (retry · resume · cancel · wake). A **cross-process,
independently-deployable** boundary, so it is contract-bounded under **P4** even though both sides are
Python — flows-api is its own service on its own port, reached through the gateway.

**Flows are DATA, never code.** A submission is a name + trigger + an ordered list of step names from a
fixed vocabulary (`FlowList.steps_vocabulary`). A name outside the vocabulary is a `400 UnknownSteps`
with the vocabulary returned — the P4 forcing function: a wrong step is a *contract* error the caller
can self-correct, not a runtime failure. A reaction is a fact walking a flow's steps; `next_run_at` is
the parked wake time that lets a scheduled bot dispatch or a blocked gate wait at zero cost. Signals are
audited state transitions, never table surgery.

Shapes (`#/$defs`): `FlowSubmission` `Flow` `StepDoc` `FlowList` `SubmitResult` `StatusResult`
`UnknownSteps` `Reaction` `ReactionList` `SignalResult`.

**Status: UNSEALED** (stub) — the schema + goldens are pinned and `gate:schema` validates them; the
contract is sealed into `contracts.seal.json` when flows-api becomes a stack service behind the gateway
(the wiring this contract makes possible). Until then flows-api runs beside the stack (see
`architecture.calm.json` — the `flows` node states the gap).

Carriers this service owns (declared in `architecture.calm.json`, P23, one writer = the flows worker):
`reaction` · `signal` · `flow-version` · `effect-receipt` · `mail-thread` · `mail-cursor` ·
`mail-outbox-sent` — all in the `flows` postgres database.
