"""The persona decision — one Haiku call per REAL touch, reading the actual mail the flows
engine sent, that persona's brief, and its own history of what it did with earlier touches.

Haiku everywhere (founder: "if you are able to get good results with Haiku, that means we get
there"). The call is deliberately isolated from this host's own Claude configuration: bbb's
`~/.claude/settings.json` carries a `Stop` hook that blocks the turn and demands a TLDR, which
turned every judgment into the model agreeing to add a TLDR. CLAUDE_CONFIG_DIR points at a
scratch config whose `.credentials.json` is a SYMLINK to the real one — nothing is copied, and
the founder's configuration is not touched.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import personas

CFG = os.environ.get("SIM_CLAUDE_CFG", "/tmp/haiku-cfg")
MODEL = os.environ.get("SIM_MODEL", "haiku")
CWD = os.environ.get("SIM_CLAUDE_CWD", "/tmp/haiku-clean")


def ensure_cfg() -> None:
    """Scratch config: no hooks, no project CLAUDE.md, credentials by symlink."""
    os.makedirs(CWD, exist_ok=True)
    if not os.path.isdir(CFG):
        os.makedirs(CFG, mode=0o700, exist_ok=True)
    link = os.path.join(CFG, ".credentials.json")
    if not os.path.exists(link):
        os.symlink(os.path.expanduser("~/.claude/.credentials.json"), link)
    with open(os.path.join(CFG, "settings.json"), "w") as f:
        f.write("{}\n")


def _ask(prompt: str, timeout=240, tries=2) -> str:
    """One Haiku answer.

    The prompt is passed INLINE. It used to be written to a temp file and referenced as
    `@/tmp/<file>` — Claude Code's file-reference syntax — from a working directory that does
    not contain that path, so the reference could not resolve and 38% of calls came back empty.
    Empty answers were then dropped from the aggregate, which is the worst possible handling:
    the rates looked clean and were built on 62% of the sample, with no way to see it."""
    env = dict(os.environ, CLAUDE_CONFIG_DIR=CFG)
    for attempt in range(tries):
        try:
            r = subprocess.run(
                ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json"],
                cwd=CWD, env=env, capture_output=True, text=True, timeout=timeout)
            try:
                out = json.loads(r.stdout)
                if out.get("is_error"):
                    continue
                txt = out.get("result", "") or ""
            except Exception:  # noqa: BLE001
                txt = r.stdout[:4000]
            if txt.strip():
                return txt
        except subprocess.TimeoutExpired:
            continue
    return ""


def _json_out(text: str) -> dict | None:
    """First balanced JSON object in the answer — robust to a fence or a stray sentence."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    depth, start = 0, None
    for i, c in enumerate(t):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:  # noqa: BLE001
                    start = None
    return None


HIST_LINE = "day {day}: {kind} — you {outcome}{because}"


def _history_block(history: list) -> str:
    if not history:
        return "This is the first thing you have ever received from this tool."
    out = []
    for h in history[-8:]:
        because = f" ({h['why']})" if h.get("why") else ""
        out.append(HIST_LINE.format(day=h.get("day", "?"), kind=h.get("kind", "a message"),
                                    outcome=h.get("outcome", "ignored"), because=because))
    return "\n".join(out)


PROMPT = """You are simulating one person's reaction to one email, honestly and in character.

WHO YOU ARE
{brief}

Your role at the studio: {role}, in {dept}. Your name is {name}.

WHAT HAS ALREADY HAPPENED TO YOU
{history}

Today you also received {load} other automated emails.

THE EMAIL THAT JUST ARRIVED (verbatim — this is the real text the product sent)
Subject: {subject}
---
{text}
---
{linknote}

DECIDE
Answer as yourself. Opening an email is NOT using something — be strict about the difference
between glancing at a message and actually doing something with it. If the last few things this
tool sent you were useless, say so and ignore this one; people stop opening things that waste
their time. If it is genuinely about your work, act.

Return ONLY a JSON object, no prose and no code fence, with exactly these keys:
{schema}
"""


def decide(person, touch: dict, history: list, day: int = 0, load: int = 0) -> dict:
    brief = personas.PERSONAS[person.persona][1]
    linknote = ("There is a link in it that opens a Vexa chat about this meeting."
                if touch.get("links") else "There is no link in it.")
    prompt = PROMPT.format(
        brief=brief, role=person.role.replace("_", " "), dept=person.dept, name=person.name,
        history=_history_block(history), load=load,
        subject=touch.get("subject", ""), text=(touch.get("text") or "")[:4000],
        linknote=linknote,
        schema=json.dumps(personas.SCHEMA, indent=1))
    out = _json_out(_ask(prompt))
    if not out:
        return {"opened": False, "outcome": "ignored", "why": "(judge produced no answer)",
                "friction": "judge-error", "_error": True}
    for k in ("opened", "clicked", "chat_turn", "replied", "completed_setup",
              "invited_own_meeting", "forwarded"):
        out[k] = bool(out.get(k))
    if not out["opened"]:                       # an unopened mail cannot have been acted on
        for k in ("clicked", "chat_turn", "replied", "completed_setup",
                  "invited_own_meeting", "forwarded"):
            out[k] = False
    out["active_action"] = any(out.get(k) for k in personas.UI_ACTIONS)
    out["outcome"] = out.get("outcome") if out.get("outcome") in (
        "acted", "hesitated", "ignored") else ("acted" if out["active_action"] else "ignored")
    return out


def decide_many(jobs: list, workers: int = 8) -> list:
    """jobs = [(person, touch, history, day, load)] -> answers in the same order."""
    ensure_cfg()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda j: decide(*j), jobs))


# ── the conversation with the REAL agent, once a persona clicked ─────────────────────────────
CONV_SCHEMA = {
    "got_value": "bool — did that answer actually give you something you did not have",
    "asked_more": "bool — are you going to ask it another thing",
    "corrected": "bool — did it get something wrong that you are correcting",
    "asked_action": "invite_mailbox | set_up_group | change_setting | none",
    "abandoned": "bool — are you closing the tab now",
    "say": "str — what you type next, in your own words (empty if you are closing the tab)",
    "why": "str — one sentence, first person",
}

CONV_OPEN = """You just clicked the link in that email and a chat opened. You are {name}, {role}
in {dept}.

{brief}

You have about {seconds} seconds of patience for this.

THE CHAT OPENED WITH THIS (verbatim, from the product):
---
{opening}
---

Type your first message to it, or close the tab. Return ONLY a JSON object with exactly these
keys: {schema}"""

CONV_TURN = """You are {name}, {role} in {dept}, in the middle of a chat you opened from an
email.

{brief}

WHAT YOU SAID: {said}
WHAT IT ANSWERED (verbatim):
---
{answer}
---

Return ONLY a JSON object with exactly these keys: {schema}"""


def conv_open(person, opening: str, seconds: int = 60) -> dict:
    out = _json_out(_ask(CONV_OPEN.format(
        name=person.name, role=person.role.replace("_", " "), dept=person.dept,
        brief=personas.PERSONAS[person.persona][1], seconds=seconds,
        opening=(opening or "")[:3000], schema=json.dumps(CONV_SCHEMA, indent=1))))
    return out or {"abandoned": True, "say": "", "why": "(judge produced no answer)",
                   "got_value": False, "asked_action": "none"}


def conv_turn(person, said: str, answer: str) -> dict:
    out = _json_out(_ask(CONV_TURN.format(
        name=person.name, role=person.role.replace("_", " "), dept=person.dept,
        brief=personas.PERSONAS[person.persona][1], said=(said or "")[:600],
        answer=(answer or "")[:3000], schema=json.dumps(CONV_SCHEMA, indent=1))))
    return out or {"abandoned": True, "say": "", "why": "(judge produced no answer)",
                   "got_value": False, "asked_action": "none"}
