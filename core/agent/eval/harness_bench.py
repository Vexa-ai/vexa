#!/usr/bin/env python3
"""harness_bench.py — measure ANY harness runner on real DNA fixtures, offline.

PRD decision 37's order: *build → measure on the DNA replay against the Haiku baseline → switch the
scratch/sim lane at a window*. This is the measuring half, and it is committed rather than left in
`/tmp` (where the write-back A/B that produced the ledger's baseline actually lived) for one reason:
a number nobody can reproduce is a number nobody can argue with.

It runs the REAL turn engine — `worker.engine.run_turn_over_workspace`, the real preambles, the real
write-back phase with its real budget — over a scratch workspace seeded from `behavior/workspaces/
default`, with the entity tool served by `eval/entity_mcp_stub.py` over stdio. Nothing running is
touched: no rig, no docker, no stack, no mailpit.

    python3 core/agent/eval/harness_bench.py --fixtures ~/dna-fixtures --out ~/dna-runs/oa \\
        --dates 2026-03-02 2026-03-16 --runner openai-agent --model qwen3.8-27b

    # the Haiku baseline the ledger carries
    python3 core/agent/eval/harness_bench.py ... --runner claude-code --model haiku

Per fixture it reports what the ledger reports, plus the two dimensions a NEW runner has to earn:

    entities_touched  the DNA scorer's own dimension (`core/flows/eval/dna/score.py`)
    pages             entity pages on disk after the turn + the phase
    names_linked      the scorer's wikilink discipline measure (and `bare`, its denominator)
    answer_s/phase_s  wall clock, the bottleneck the trimmed phase was built for
    tool_ok           share of tool calls that returned ok — a loop that cannot call is not an agent
    json_valid        share of tool calls whose ARGUMENTS parsed — the Qwen thinking-mode failure
                      mode, visible as unparsable function arguments rather than as a bad answer

A rate-limited or refusing model is a VOID row, never a low score (the ledger's own lesson).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]                                   # …/core/agent/eval/x.py → repo root
sys.path.insert(0, str(REPO / "core/agent"))
sys.path.insert(0, str(REPO / "core/flows/eval/dna"))

TRANSCRIPT_CAP = 40_000
_LIMIT = ("hit your limit", "rate limit", "usage limit", "credit balance", "quota")

KICK = """A meeting you were in has finished. Write it up.

Meeting: {title}
Date: {date}

Write the meeting note to `kg/entities/meeting/{date}-dna-tsc.md` — frontmatter (type, id, title),
then `## Decided`, `## Committed` (each item with its owner), `## Open`. Every item attributed.
Write only what the transcript says.

Then reply to me with the report itself — the same words everyone in the room will read.

=== TRANSCRIPT ===
{transcript}
"""


def _void(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _LIMIT)


def workspace(out: Path, tag: str, seed: Path) -> Path:
    ws = out / tag
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(seed, ws)
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    for k, v in (("user.email", "bench@rehearsal.test"), ("user.name", "bench")):
        subprocess.run(["git", "-C", str(ws), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "seed"], check=True)
    return ws


def transcript_of(fx: dict) -> str:
    return "\n".join(f"{s.get('speaker', '?')}: {s.get('text', '')}"
                     for s in fx["segments"])[:TRANSCRIPT_CAP]


def entity_files(ws: Path) -> list[str]:
    base = ws / "kg" / "entities"
    return sorted(str(f.relative_to(ws)) for f in base.rglob("*.md") if f.name != "index.md")


def note_of(ws: Path) -> str:
    d = ws / "kg" / "entities" / "meeting"
    files = [f for f in sorted(d.glob("*.md")) if f.name != "index.md"] if d.is_dir() else []
    return files[0].read_text() if files else ""


def _last_assistant_text(ws: Path) -> str:
    out = ""
    for f in sorted((ws / ".claude" / "projects").rglob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "assistant":
                continue
            for b in (e.get("message", {}) or {}).get("content", []) or []:
                if b.get("type") == "text" and b.get("text"):
                    out = b["text"]
    return out


class _Tally:
    """Tool-call health, read off the event stream: how many calls, how many returned ok, and how
    many arrived with parsable arguments. The third is the one that separates a model that can drive
    a loop from one that only looks like it can."""

    def __init__(self) -> None:
        self.calls = self.ok = self.parsed = 0

    def call(self, ev: dict) -> None:
        self.calls += 1
        args = ev.get("args")
        if isinstance(args, dict) and "__unparsed_arguments__" not in args:
            self.parsed += 1

    def result(self, ev: dict) -> None:
        self.ok += 1 if ev.get("ok") else 0

    def as_dict(self, prefix: str = "") -> dict:
        n = max(1, self.calls)
        return {f"{prefix}calls": self.calls,
                f"{prefix}tool_ok": round(self.ok / n, 3) if self.calls else None,
                f"{prefix}json_valid": round(self.parsed / n, 3) if self.calls else None}


def run_fixture(ws: Path, prompt: str, *, harness, model: str, engine, stub: Path) -> dict:
    mounts = [{"slug": ws.name, "path": str(ws), "write": True, "primary": True, "role": "private"}]
    engine.active_mounts = lambda: mounts
    os.environ["VEXA_MOUNTS"] = json.dumps(mounts)

    answer, phase = _Tally(), _Tally()
    t0 = time.time()
    said, upserts, reply = [prompt], 0, ""
    for ev in engine.run_turn_over_workspace(
            ws, prompt, model=model, allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            commit=False, session="bench", harness=harness):
        t = ev.get("type")
        if t == "tool-call":
            answer.call(ev)
            if str(ev.get("tool") or "").endswith("entity_upsert"):
                upserts += 1
        elif t == "tool-result":
            answer.result(ev)
            if ev.get("summary"):
                said.append(str(ev["summary"]))
        elif t == "message-delta" and ev.get("text"):
            said.append(ev["text"])
        elif t == "done":
            reply = ev.get("reply") or ""
    out = {"answer_s": round(time.time() - t0, 1), "prepass_s": 0.0, "phase_s": 0.0,
           "model_call": False, "candidates": [], "truncated": None, "phase_reply": "",
           "void": _void(reply), "reply": reply, **answer.as_dict()}

    t1 = time.time()
    cands = (engine.writeback_candidates(said, mounts)
             if engine.should_write_back(prompt, answer.calls, upserts=upserts) else [])
    out["prepass_s"] = round(time.time() - t1, 2)
    out["candidates"] = cands
    if not engine.should_write_back(prompt, answer.calls, upserts=upserts, candidates=cands):
        out["phase_s"] = out["prepass_s"]
        return out

    # THE TOOL PATH, which is what production takes. Without it the phase hand-writes each card as
    # markdown — about four times the tokens — and the measurement reports the cost of a path only a
    # dispatch with no delegation token ever takes.
    cfg = ws / ".claude" / "entities-mcp.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": {"entities": {
        "type": "stdio", "command": sys.executable, "args": [str(stub), str(ws)]}}}))
    tools = [*engine.WRITEBACK_TOOLS, "mcp__entities", "mcp__entities__entity_upsert"]
    max_calls, max_s = engine.writeback_budget()
    out["model_call"] = True
    for ev in engine.writeback_events(engine.bounded(
            engine.run_turn_over_workspace(
                ws, engine.writeback_prompt(cands), model=model, allowed_tools=tools,
                mcp_config=str(cfg), commit=False, session="bench", harness=harness),
            max_tool_calls=max_calls, max_seconds=max_s)):
        if ev.get("type") == "tool-call":
            phase.call(ev)
        elif ev.get("type") == "tool-result":
            phase.result(ev)
        elif ev.get("type") == "writeback-truncated":
            out["truncated"] = ev.get("reason")
    reply2 = _last_assistant_text(ws)
    out["phase_reply"] = reply2[:400]
    out["void"] = out["void"] or _void(reply2)
    out["phase_s"] = round(time.time() - t1, 1)
    out.update(phase.as_dict("phase_"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dates", nargs="+", required=True)
    ap.add_argument("--runner", default=os.environ.get("VEXA_RUNNER") or "claude-code")
    ap.add_argument("--model", default=os.environ.get("VEXA_LLM_MODEL") or "haiku")
    ap.add_argument("--seed", type=Path, default=REPO / "behavior/workspaces/default")
    args = ap.parse_args()

    os.environ["VEXA_RUNNER"] = args.runner
    from llm.registry import HARNESS_RUNNERS                       # noqa: E402
    from worker import engine                                      # noqa: E402
    import score as S                                              # noqa: E402

    if args.runner not in HARNESS_RUNNERS:
        print(f"unknown runner {args.runner!r} — known: {sorted(HARNESS_RUNNERS)}")
        return 2
    harness = HARNESS_RUNNERS[args.runner]()
    stub = HERE.parent / "entity_mcp_stub.py"
    args.out.mkdir(parents=True, exist_ok=True)

    rows = []
    for date in args.dates:
        fx = json.loads((args.fixtures / f"{date}.transcript.json").read_text())
        prompt = KICK.format(title=fx["meeting"]["title"], date=date, transcript=transcript_of(fx))
        ws = workspace(args.out, f"{date}-{args.runner}", args.seed)
        print(f"\n=== {date} · {args.runner} · {args.model} ===", flush=True)
        res = run_fixture(ws, prompt, harness=harness, model=args.model, engine=engine, stub=stub)
        files = entity_files(ws)
        rec = {"note": note_of(ws), "entity_files": files, "entity_turns": 1}
        ent, _ = S.d_entities_touched(rec)
        nm, ev_n = S.d_names_linked(rec)
        row = {"date": date, "runner": args.runner, "model": args.model,
               "entities_touched": ent, "pages": len(files), "names_linked": nm,
               "bare": ev_n.get("count"), "files": files,
               **{k: v for k, v in res.items() if k != "reply"}}
        print(json.dumps({k: v for k, v in row.items()
                          if k not in ("files", "candidates", "phase_reply")}, indent=1), flush=True)
        rows.append(row)
        (args.out / f"{date}-{args.runner}.json").write_text(
            json.dumps({**row, "reply": res["reply"]}, indent=1))
        if row["void"]:
            (args.out / "bench.json").write_text(json.dumps(rows, indent=1))
            print("\nVOID — the model refused or was throttled; every number after this point would "
                  "measure the limiter. Stopping.", flush=True)
            return 2
    (args.out / "bench.json").write_text(json.dumps(rows, indent=1))

    n = max(1, len(rows))
    def avg(k):
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return sum(vals) / len(vals) if vals else float("nan")
    print("\n" + "=" * 92)
    print(f"{args.runner}/{args.model}: entities_touched {avg('entities_touched'):.3f} · "
          f"pages/fixture {avg('pages'):.1f} · names_linked {avg('names_linked'):.3f} · "
          f"answer_s {avg('answer_s'):.1f} · phase_s {avg('phase_s'):.1f} · "
          f"tool_ok {avg('tool_ok'):.3f} · json_valid {avg('json_valid'):.3f} · n={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
