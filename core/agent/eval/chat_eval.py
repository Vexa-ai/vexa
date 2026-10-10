#!/usr/bin/env python3
"""chat_eval.py — does a model take the right next step in a recorded chat? Run by hand.

A case (`chat_cases/<name>.json`) is a short conversation that ends on the person's message, the
tools the agent holds (their descriptions and argument schemas taken from what agent-api serves,
`core/agent/mcp.tools.v1.json` + `mcp.tools.v1.openapi.json`), the standing guidance every dispatch
carries (`worker.engine.connections_preamble`), and per variant the answer a failing tool gave and
what counts as a pass. The model's next step is scored:

    calls:<tool>        it calls that tool
    explains:outage     it calls nothing, and its text says the service is down / unavailable and
                        that reconnecting will not fix it
    (always)            it never calls the tool that failed

    VEXA_EVAL_BASE_URL=https://openrouter.ai/api/v1 VEXA_EVAL_API_KEY=... VEXA_EVAL_MODEL=... \\
        python3 core/agent/eval/chat_eval.py connect-gmail [--runs 3]

Any OpenAI-compatible chat completions endpoint works; the key is optional for an open one. Nothing running is touched: no stack, no
broker, no workspace. Scoring is `score()`, which `tests/test_chat_eval.py` holds offline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).resolve().parent / "chat_cases"

_OUTAGE = re.compile(r"\b(unavailable|outage|down|not available)\b", re.I)
_NO_FIX = re.compile(r"(won.t|will not|wouldn.t|would not|doesn.t|does not|can.t|cannot)\b[^.]{0,60}\b(fix|help|solve|resolve)"
                     r"|reconnect(ing)?\b[^.]{0,40}\b(won.t|will not|doesn.t|does not|not)\b", re.I)


def load_case(name: str) -> dict:
    return json.loads((CASES / f"{name}.json").read_text())


def tool_specs(names: list[str]) -> list[dict]:
    """OpenAI tool definitions for the named agent tools, as agent-api serves them."""
    manifest = json.loads((AGENT / "mcp.tools.v1.json").read_text())
    openapi = json.loads((AGENT / "mcp.tools.v1.openapi.json").read_text())
    out = []
    for tool in manifest["tools"]:
        if tool["name"] not in names:
            continue
        op = openapi["paths"][tool["route"]["path"]][tool["route"]["method"].lower()]
        schema = {"type": "object", "properties": {}}
        ref = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
        if ref and "$ref" in ref:
            schema = openapi["components"]["schemas"][ref["$ref"].rsplit("/", 1)[1]]
        out.append({"type": "function", "function": {"name": tool["name"],
                                                      "description": op.get("description", "")[:4000],
                                                      "parameters": json.loads(json.dumps(schema))}})
    return out


def messages(case: dict, variant: str, system: str) -> list[dict]:
    v = case["variants"][variant]
    out = [{"role": "system", "content": system}]
    call_id = 0
    for turn in case["turns"]:
        if turn.get("tool_call"):
            call_id += 1
            out.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{call_id}", "type": "function",
                "function": {"name": turn["tool_call"]["name"], "arguments": json.dumps(turn["tool_call"]["arguments"])}}]})
        elif turn["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": f"call_{call_id}",
                        "content": json.dumps({"status": v["status"], "detail": v["detail"]})})
        else:
            out.append({"role": turn["role"], "content": turn["content"]})
    return out


def score(case: dict, variant: str, reply: dict) -> tuple[bool, str]:
    """``reply`` is the model's assistant message: ``{"content": str|None, "tool_calls": [...]}``."""
    calls = [c["function"]["name"] for c in (reply.get("tool_calls") or [])]
    text = reply.get("content") or ""
    if case["failing_tool"] in calls:
        return False, f"retried {case['failing_tool']}"
    for rule in case["variants"][variant]["pass"]:
        kind, _, arg = rule.partition(":")
        if kind == "calls" and arg in calls:
            return True, f"called {arg}"
        if kind == "explains" and arg == "outage" and not calls and _OUTAGE.search(text) and _NO_FIX.search(text):
            return True, "explained the outage"
    return False, f"calls={calls or 'none'}; text={text[:160]!r}"


def _complete(base: str, key: str, model: str, msgs: list[dict], tools: list[dict]) -> dict:
    body = json.dumps({"model": model, "messages": msgs, "tools": tools, "max_tokens": 400}).encode()
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args(argv)
    base, key, model = (os.environ.get(k, "") for k in ("VEXA_EVAL_BASE_URL", "VEXA_EVAL_API_KEY", "VEXA_EVAL_MODEL"))
    if not (base and model):
        print("set VEXA_EVAL_BASE_URL and VEXA_EVAL_MODEL (and VEXA_EVAL_API_KEY unless the endpoint is open)",
              file=sys.stderr)
        return 2
    sys.path[:0] = [str(AGENT), str(AGENT.parent)]   # core/agent, and core/ for `workspaces`
    from worker.engine import connections_preamble

    case = load_case(args.case)
    system = "You are the person's assistant in Vexa Minutes.\n\n" + connections_preamble()
    tools = tool_specs(case["tools"])
    failed = 0
    for variant in case["variants"]:
        for run in range(args.runs):
            reply = _complete(base, key, model, messages(case, variant, system), tools)
            ok, why = score(case, variant, reply)
            failed += not ok
            calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments")}
                     for c in (reply.get("tool_calls") or [])]
            print(json.dumps({"case": case["case"], "variant": variant, "run": run + 1, "pass": ok, "why": why,
                              "tool_calls": calls, "text": (reply.get("content") or "")[:600]}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
