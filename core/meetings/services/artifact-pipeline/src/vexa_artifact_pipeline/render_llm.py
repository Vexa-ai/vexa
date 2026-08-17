"""The model-driven renderer — declared, not implemented.

This is the renderer the product actually wants. The deterministic one
(:mod:`.render_template`) can only quote sentences a cue matched; the highest-value lines
in the founder's own archive were **derived deltas nobody said aloud** — "the November raise
needs Alexey's reference letter before he's back in September" — which is exactly what a
model over the whole record with the recipient's context in front of it produces. It is also
where the product can be confidently wrong, which is why the two live behind one port: the
same corpus can be rendered both ways and the difference read, rather than argued.

**Why it raises instead of shipping.** It needs a model route, and there is no model routing
in this service or anywhere near it. Per the product spec §2/§3 the route is the
**workspace's own token (BYOT)** — the workspace owner supplies an LLM key or an
endpoint+key, stored write-only (set/rotate; reads return last-4 only), resolved through the
workspace's context stack at render time. Two things must land before this class has
anything to call:

1. **The BYOT decision itself is open** (spec §3): BYOT-only, or a global token with
   prepaid credits. The recommendation on the table is BYOT-only for Stage 0–1 — but it is
   the founder's call and nothing here should pre-empt it.
2. **The credential-leak advisories are prerequisites** — a product that asks for your key
   must not log it (secrets in spawn-failure logs; a settings read returning key material).

A stub that quietly fell back to the template renderer would make the pipeline *look*
model-driven in a run log while producing cue-matched sentences, so it raises.
"""

from __future__ import annotations

from typing import Sequence

from .artifact import Artifact, Recipient
from .ports import FetchedRecord


class LlmRenderer:
    """Renders the context delta with the workspace's own model. Not implemented.

    The eventual call shape, so the port is honest about what it needs: the record and its
    roster, the recipient's identity, and the resolved workspace context stack (global →
    group → personal → user-system) whose group and personal layers supply "their context"
    — the thing the delta is a delta *of*. The model route comes from the workspace's BYOT
    configuration, which is why this class takes a workspace handle it cannot yet be given.
    """

    name = "llm"

    def __init__(self, *, workspace: object | None = None) -> None:
        self._workspace = workspace

    def render(
        self,
        *,
        record: FetchedRecord,
        recipient: Recipient,
        participants: Sequence[Recipient],
        meeting_id: str,
        meeting_label: str,
        language: str,
    ) -> Artifact:
        raise NotImplementedError(
            "LlmRenderer needs the workspace's BYOT model route (an LLM key, or an "
            "endpoint+key, held write-only on the workspace and resolved through its "
            "context stack). No model routing exists in this service, and the BYOT-vs-"
            "global-token decision is open. Run the pipeline with TemplateRenderer until "
            "both land."
        )


__all__ = ["LlmRenderer"]
