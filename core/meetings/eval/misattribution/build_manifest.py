#!/usr/bin/env python3
"""Assemble the fixture manifest from per-meeting scorer verdicts.

Emits only ids, spans lengths, signal types, verdicts and tape pointers — the
schema in `manifest.schema.json` forbids anything else, and this builder never
reads transcript text into the output.

Tiering
  gold    the lane recorded a cross-check signal (csrc frames + observations)
          alongside the audio, so the linguistic verdict can be confirmed
          mechanically offline: replay the tape, check track separation and
          roster timing, and see the same answer.
  silver  linguistic signal only. Either no session tape survives, or the lane
          records none (the GMeet channel lane writes no csrc/observations at
          all — the known capture gap from #1221).
"""

from __future__ import annotations

import argparse
import json
import pathlib
from datetime import datetime, timezone


def lane_of(track: str) -> str:
    if track.startswith("csrc-"):
        return "csrc"
    if track.startswith("ch-"):
        return "channel"
    if track in ("turn", ""):
        return "mixed"
    return "unkeyed"


def _tier_reason(gold: bool, tape: dict | None, mechanical: bool, lane: str) -> str:
    if gold:
        return ("tape carries csrc + observations; separation and roster timing replay "
                "offline and agree with the linguistic verdict")
    if not tape:
        return "linguistic signal only — no session tape retained"
    if mechanical and lane != "csrc":
        # 26042's shape: the audio WAS separable on the tape, but the lane that
        # produced this transcript wrote one undifferentiated bucket, so no
        # segment can be tied back to a csrc track. That gap is the bug, and it
        # is also why the fixture cannot be promoted.
        return ("tape carries csrc + observations, but this transcript's lane emitted no "
                f"per-track key ({lane}) — the linguistic verdict cannot be tied to a track")
    if mechanical:
        return "tape carries csrc + observations; offline replay not yet run against this fixture"
    return "linguistic signal only — lane records no csrc/observations cross-check signal"


def build(rows: list[dict], corpus: dict, calibration: dict, judge: dict) -> dict:
    fixtures = []
    for r in rows:
        v, m = r["verdict"], r["meeting"]
        if v["verdict"] != "MISLABELED":
            continue
        tape = r.get("tape")
        parts = (tape or {}).get("parts") or []
        mechanical = bool(tape) and "csrc" in parts and "observations" in parts
        gold = mechanical and lane_of(v["track"]) == "csrc" and r.get("tape_verified")
        fixtures.append({
            "fixture_id": f"mis-{m['id']}-{v['track']}",
            "meeting_id": m["id"],
            "platform": m["platform"],
            "lane": lane_of(v["track"]),
            "track": v["track"],
            "label": v["label"],
            "implied_label": v.get("implied_label"),
            "verdict": v["verdict"],
            "tier": "gold" if gold else "silver",
            "tier_reason": _tier_reason(gold, tape, mechanical, lane_of(v["track"])),
            "segments": v["segments"],
            "tape": tape,
            "evidence": [
                {
                    "segment_id": c["segment_id"], "signal": c["signal"],
                    "named": c["named"],
                    "direction": "not_speaker" if c["signal"] == "vocative" else "is_speaker",
                    "label": c["label"], "t_start": c.get("t_start"),
                    "span_len": len(c.get("quote_span") or "") or None,
                }
                for c in v["contradictions"]
            ],
        })
    fixtures.sort(key=lambda f: (f["tier"] != "gold", -len(f["evidence"]), f["fixture_id"]))
    return {
        "version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "judge": judge,
        "corpus": corpus,
        "signals": ["vocative", "self_id"],
        "calibration": calibration,
        "fixtures": fixtures,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True, help="JSON array of {meeting, verdict, tape, tape_verified}")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    man = build(
        json.loads(pathlib.Path(a.rows).read_text()),
        json.loads(pathlib.Path(a.corpus).read_text()),
        json.loads(pathlib.Path(a.calibration).read_text()),
        json.loads(pathlib.Path(a.judge).read_text()),
    )
    pathlib.Path(a.out).write_text(json.dumps(man, indent=1) + "\n")
    tiers = {}
    for f in man["fixtures"]:
        tiers[f["tier"]] = tiers.get(f["tier"], 0) + 1
    print(f"fixtures={len(man['fixtures'])} {tiers} -> {a.out}")


if __name__ == "__main__":
    main()
