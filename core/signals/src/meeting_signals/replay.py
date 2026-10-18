"""Replay a transcript through the listener and print the wakes it would send.

    python -m meeting_signals.replay transcript.json [--brief brief.json] [--model fake]
        [--model-signals '{"adapter":"jev","route":"typesafe/jev-1.13","secret_ref":"none"}']
        [--batch-seconds 2] [--wakes-cap 12] [--cost-cap 0.5] [--usd-per-mtok 2.0]

The same ``Listener`` the service runs, on a simulated clock: segments are released in order of their
end time, in batches of ``--batch-seconds`` of meeting time (as lines would reach the feed), and
``max_wait_s`` is measured in meeting time. ``transcript.json`` is a list of segments, or an object
with ``segments`` (the Vexa transcript API's shape): ``speaker``, ``text``, ``start``, ``end``, and
optionally ``segment_id`` and ``completed`` (false = skipped, like an unconfirmed draft).

``--model fake`` is a deterministic keyword scorer (no network) for checking the mechanics. A real
model needs ``--model-signals``; its key is read from ``SIGNALS_REPLAY_API_KEY`` (never printed).

Output: one JSON line per wake — time, reason, line count, score — and a summary with counts, model
calls and estimated cost. TRANSCRIPT TEXT IS NEVER PRINTED unless ``--show-text`` is given, so the
output of a replay over a private meeting can be shared.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from . import brief as briefs
from . import model as model_mod
from .core import Caps, Listener, State
from .model import Line, Scored, estimate_tokens
from .policy import parse_model


def load_lines(path: str) -> list:
    data = json.loads(Path(path).read_text())
    rows = data.get("segments", []) if isinstance(data, dict) else data
    out = []
    for i, s in enumerate(rows):
        text = str(s.get("text") or "").strip()
        if not text or s.get("completed") is False:
            continue
        start = float(s.get("start", s.get("start_time", 0)) or 0)
        end = float(s.get("end", s.get("end_time", start)) or start)
        out.append(Line(str(s.get("segment_id") or f"r{i}"), str(s.get("speaker") or "Unknown speaker"), text,
                        start, end))
    return sorted(out, key=lambda l: (l.end, l.start))


_FAKE = {
    briefs.ADDRESSED: re.compile(r"\bvexa\b", re.I),
    "decision": re.compile(r"\b(we decided|let'?s go with|agreed|we will go|decision)\b", re.I),
    "action_item": re.compile(r"\b(i will|i'll|you will|can you|please|todo|action item|follow up)\b", re.I),
    "blocker": re.compile(r"\b(blocked|blocker|cannot|can't|problem|issue)\b", re.I),
}


async def fake_score(asks: dict, line: Line, ctx: list) -> Scored:
    scores = {k: (0.9 if (_FAKE.get(k) and _FAKE[k].search(line.text)) else 0.02) for k in asks}
    return Scored(scores, estimate_tokens(asks, line, ctx), "fake")


async def replay(lines: list, brief: briefs.Brief, scorer, caps: Caps, batch_seconds: float) -> dict:
    clock = {"t": 0.0}
    listener = Listener({"kind": "meeting", "id": "replay"}, scorer=scorer, caps=caps, now=lambda: clock["t"])
    state, wakes, calls, started = State(), [], 0, time.monotonic()
    t0 = lines[0].end if lines else 0.0

    def wake(reason: str) -> None:
        data = listener.wake_data(state, brief, reason, {"meeting_row": "0"})
        wakes.append({"t_s": round(clock["t"] - t0, 1), "reason": reason, "lines": len(data["buffer"]),
                      "score": data["score"],
                      **({"text": [b["text"] for b in data["buffer"]]} if SHOW_TEXT else {})})
        listener.woke(state)

    i = 0
    while i < len(lines):
        batch_end = lines[i].end + batch_seconds
        batch = []
        while i < len(lines) and lines[i].end <= batch_end:
            batch.append(lines[i])
            i += 1
        # a quiet stretch before this batch: the max_wait timer would have fired during it
        if state.since is not None and batch[-1].end - state.since >= brief.max_wait_s:
            clock["t"] = state.since + brief.max_wait_s
            reason = listener.due(state, brief, [])
            if reason:
                wake(reason)
        clock["t"] = batch[-1].end
        before = state.cost_usd
        _ann, scored = await listener.take(state, batch, brief)
        calls += len(_ann)
        reason = listener.due(state, brief, scored)
        if reason:
            wake(reason)
    if state.buffer:
        wake("end")
    return {"wakes": wakes, "lines": len(lines), "model_calls": calls, "cost_usd_est": round(state.cost_usd, 4),
            "refused": state.refusals, "wall_s": round(time.monotonic() - started, 1)}


SHOW_TEXT = False


def main(argv=None) -> int:
    global SHOW_TEXT
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("transcript")
    p.add_argument("--brief")
    p.add_argument("--model", choices=["fake", "policy"], default="fake")
    p.add_argument("--model-signals")
    p.add_argument("--batch-seconds", type=float, default=2.0)
    p.add_argument("--wakes-cap", type=int, default=12)
    p.add_argument("--cost-cap", type=float, default=0.5)
    p.add_argument("--usd-per-mtok", type=float, default=2.0)
    p.add_argument("--limit", type=int, default=0, help="only the first N lines")
    p.add_argument("--show-text", action="store_true")
    a = p.parse_args(argv)
    SHOW_TEXT = a.show_text
    brief = briefs.normalize(json.loads(Path(a.brief).read_text())) if a.brief else briefs.DEFAULT
    if a.model_signals:
        m = parse_model(a.model_signals)
        key = os.environ.get("SIGNALS_REPLAY_API_KEY") if m["secret_ref"] != "none" or os.environ.get(
            "SIGNALS_REPLAY_API_KEY") else None
        scorer = model_mod.build(m, key).score
    else:
        scorer = fake_score
    lines = load_lines(a.transcript)
    if a.limit:
        lines = lines[: a.limit]
    out = asyncio.run(replay(lines, brief, scorer, Caps(a.wakes_cap, a.cost_cap, a.usd_per_mtok), a.batch_seconds))
    for w in out.pop("wakes"):
        print(json.dumps({"wake": w}))
        out.setdefault("by_reason", {}).setdefault(w["reason"], 0)
        out["by_reason"][w["reason"]] += 1
    print(json.dumps({"summary": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
