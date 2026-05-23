#!/usr/bin/env python3
"""Propose atomic pack epics from collected raw issues."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


GROUPS = [
    (
        "recording-trust",
        ["record", "playback", "audio", "chunk", "webm", "master", "finalizer", "media_file"],
        "Restore Recording Trust",
        "Completed meetings have one trustworthy recording artifact and playback reaches canonical master media end-to-end.",
        "Recording storage/finalization has a single invariant: JSONB/canonical playback metadata points at the finalized master artifact.",
        "Users can open a completed meeting and trust that playback is complete, not just the first chunk.",
    ),
    (
        "speak-and-meeting-actions",
        ["speak", "tts", "voice", "teams", "gmeet", "google meet", "join", "admission", "camera"],
        "Restore Speech And Meeting Actions",
        "Live meeting actions feel reliable instead of returning success while speech or join behavior fails.",
        "TTS, bot join, and bot-observed treatment signals are validated as one live-action boundary.",
        "Users see bots join and speak predictably, with clear failure when an external platform blocks progress.",
    ),
    (
        "lifecycle-and-billing-webhook-trust",
        ["billing", "webhook", "hook", "idempot", "stopping", "delete", "lifecycle", "callback", "terminal"],
        "Restore Lifecycle And Billing Webhook Trust",
        "Terminal meeting behavior converges cleanly and completion hooks cannot double-count billing/usage.",
        "Stop/delete lifecycle and producer-side outbound-event claims share one terminal-state model.",
        "Users and operators can trust stop/delete state and billing-sensitive completion events.",
    ),
    (
        "self-hosted-browser-lite-realtime",
        ["lite", "dashboard", "websocket", "ws", "browser", "config", "cookie", "vnc", "helm", "chart"],
        "Make Self-Hosted Browser And Lite Deployments Usable",
        "Self-hosted users can open the dashboard, authenticate, and receive live updates through browser-safe public URLs.",
        "Dashboard API/WS config, auth cookies, Lite routing, and exact live transcript delivery form one edge contract.",
        "Users can run Lite/Compose without internal URL leaks or REST-only transcript illusions.",
    ),
    (
        "release-identity-and-hardening",
        ["release", "version", "semver", "chart", "helm", "security", "dependency", "dockerfile", "provenance"],
        "Keep Packaging, Identity, And Hardening Shippable",
        "Release artifacts identify the candidate truthfully and retain hardening needed to ship.",
        "Source version, image/chart metadata, dependency floors, and hardening evidence stay coherent.",
        "Operators can tell what is running and ship/rollback with confidence.",
    ),
]


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def classify(item: dict[str, Any]) -> tuple[str, tuple[str, list[str], str, str, str, str]]:
    haystack = f"{item.get('title','')} {item.get('body','')}".lower()
    best = GROUPS[-1]
    best_score = -1
    for group in GROUPS:
        score = sum(1 for keyword in group[1] if keyword in haystack)
        if score > best_score:
            best = group
            best_score = score
    return best[0], best


def issue_line(item: dict[str, Any]) -> str:
    kind = "PR" if item.get("kind") == "pull_request" else "Issue"
    return f"- {kind} #{item['number']}: {item['title']} ({item.get('url','')})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--release", default="unknown")
    parser.add_argument("--milestone")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--integration-branch", default="")
    args = parser.parse_args()

    data = json.loads(Path(args.issues).read_text(encoding="utf-8"))
    grouped: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        slug, group = classify(item)
        if slug not in grouped:
            grouped[slug] = {
                "pack_id": f"{args.release}-{slug}" if args.release != "unknown" else slug,
                "slug": slug,
                "title": group[2],
                "ceo_outcome": group[3],
                "cto_outcome": group[4],
                "user_outcome": group[5],
                "included_items": [],
                "out_of_scope": "None identified in this picking run.",
                "blast_radius": "To be confirmed during pack review; start with the services and user journeys named by included issues.",
                "contract_decisions": "No public API/schema decision accepted until the pack epic is reviewed.",
                "isolation_requirements": "Dedicated branch, worktree, Compose namespace, Lite namespace, ports, and evidence root.",
                "compose_gate": "Pack-specific Compose validation must pass before PR readiness.",
                "lite_gate": "Pack-specific Lite validation must pass before PR readiness.",
                "synthetic_gate": "Synthetic validation must run before any live/human validation.",
                "live_human_gate": "Only required when external platform behavior or human sensory observation is part of the pack outcome.",
                "pr_checklist": "- [ ] Evidence bundle attached\n- [ ] Compose validation passed\n- [ ] Lite validation passed\n- [ ] Required tests passed\n- [ ] Code review completed\n- [ ] Stitching risks documented",
                "stitching_risks": "Review for cross-pack shared files and release integration conflicts.",
                "release_id": args.release,
                "milestone": args.milestone,
                "base_branch": args.base_branch,
                "integration_branch": args.integration_branch or f"codex/release-{args.release}-pack-integration",
                "runtime_namespace": slugify(f"vexa-{args.release}-{slug}")[:48],
            }
        grouped[slug]["included_items"].append(item)

    proposals = list(grouped.values())
    for proposal in proposals:
        proposal["included_items_markdown"] = "\n".join(issue_line(item) for item in proposal["included_items"])

    payload = {
        "repo": data.get("repo"),
        "release": args.release,
        "milestone": args.milestone,
        "packs": proposals,
        "unassigned": [],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(proposals)} packs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
