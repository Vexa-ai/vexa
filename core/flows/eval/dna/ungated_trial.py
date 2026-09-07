#!/usr/bin/env python
"""How often does the agent actually READ the meeting when only a prompt asks it to?

This is the measurement behind the grounding gate. Removing the transcript copy from
`meeting.completed` was right — it was a second home for a fact the transcription domain owns, and
its 8,000-character cap was the product's ceiling. But it moved the note's correctness onto the
agent CHOOSING to call `meeting_transcript`, and a choice is not a contract.

So: dispatch the REAL post-meeting prompt N times, on N fresh sessions, against a meeting whose
words we already hold, and count two things per run — did it call the tool, and is what it wrote
actually in the meeting. The chat path has no gate, so this measures the port's failure mode
directly. The dangerous cell is `wrote a note AND not grounded`: a shallow note is visibly shallow,
a fabricated one reads exactly like a good one.

    python ungated_trial.py --meeting-id 61 --runs 6
"""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import Rig                                              # noqa: E402

AGENT_API = os.environ.get("VEXA_DNA_AGENT_API", "http://127.0.0.1:18500")


def phrases(text, n=6):
    ws = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)
            if any(len(w) >= 6 for w in ws[i:i + n])}


def dispatch(prompt, session, budget=420):
    req = urllib.request.Request(
        f"{AGENT_API}/api/chat", method="POST",
        data=json.dumps({"prompt": prompt, "session": session}).encode(),
        headers={"Content-Type": "application/json", "X-User-Id": "68"})
    seen, reply = [], ""
    try:
        with urllib.request.urlopen(req, timeout=budget) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace")
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if ev.get("type") == "tool-call":
                    seen.append(ev.get("tool", ""))
                if ev.get("type") == "done":
                    reply = ev.get("reply") or ""
                if ev.get("type") == "turn-complete":
                    break
    except Exception as e:                                        # noqa: BLE001
        return seen, reply, f"{type(e).__name__}: {e}"
    return seen, reply, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting-id", required=True)
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--prompt-file",
                    default="/home/dima/dev/wt-line/behavior/prompts/process-meeting.md")
    ap.add_argument("--token-file", default=str(os.path.expanduser("~/dna-runs/r0/.token")))
    a = ap.parse_args()

    rig = Rig(open(a.token_file).read().strip()); rig.connect()
    tx = rig.call("meeting_transcript", meeting_id=str(a.meeting_id), tail=0)
    words = "\n".join(l.get("said", "") for l in (tx.get("transcript") or [])) \
        if isinstance(tx, dict) else ""
    truth = phrases(words)
    print(f"meeting {a.meeting_id}: {tx.get('total_segments')} segments, "
          f"{len(words)} chars, {len(truth)} distinct 6-grams\n", flush=True)

    tmpl = open(a.prompt_file).read()
    prompt = (tmpl.replace("{mid}", str(a.meeting_id))
                  .replace("{date}", "2026-03-02-0000").replace("{native}", "96088138284"))

    rows = []
    for i in range(a.runs):
        tools, reply, err = dispatch(prompt, f"ungated-{int(time.time())}-{i}")
        called = any("meeting_transcript" in t for t in tools)
        wrote = len(reply.strip()) > 400
        grounded = bool(phrases(reply) & truth)
        rows.append({"run": i + 1, "called_tool": called, "wrote_note": wrote,
                     "grounded": grounded, "error": err})
        print(f"run {i+1}: called={called!s:5} wrote={wrote!s:5} grounded={grounded!s:5}"
              f"{' ERR ' + err if err else ''}", flush=True)

    ok = [r for r in rows if not r["error"]]
    called = sum(r["called_tool"] for r in ok)
    fabricated = sum(1 for r in ok if r["wrote_note"] and not r["grounded"])
    print(f"\ncalled the tool : {called}/{len(ok)}")
    print(f"grounded        : {sum(r['grounded'] for r in ok)}/{len(ok)}")
    print(f"FABRICATED      : {fabricated}/{len(ok)}   (wrote a note that is not in the meeting)")
    print(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
