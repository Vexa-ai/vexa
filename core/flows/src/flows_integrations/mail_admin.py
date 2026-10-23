"""THE GLOBAL ACCOUNT DESIGNS THE MAIL — what the `mail_*` admin tools do (founder, 2026-10-14).

*"the actual notifications should be decided by the global account, so they design and test,
starting from templates we provide."* Five verbs, all the deployment's admin account's, from chat:

    mail_templates_list           every kind: switch, template version, whether that version was
                                   test-sent, its variables, what sends it today
    mail_template_edit(kind, c)   writes `_global/mail/<template>.md` AS the admin account, through
                                   agent-api's own `_global` door (a reviewable commit)
    mail_preview(kind, sample)    renders the live template — or an unsaved draft — with sample data
    mail_test_send(kind)          sends it to the admin account's OWN address, and nowhere else
    mail_kind_set(kind, state)    writes `mail.<kind>: on|off|admins` into `_global/POLICIES.md` —
                                   REFUSED for on/admins unless the CURRENT template version has been
                                   test-sent, and refused for turning the sign-in link off

Everything here is logic over injected doors (`read_file`, `write_file`, `db`, `send`), so the whole
loop is testable offline; `flows_api` binds them to the real ones.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from flows_steps import mailkinds, mailtext, outbound, policies

TEMPLATE_MAX = 20_000
_URL = re.compile(r"https?://|www\.", re.I)


class MailAdminError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def tested_version(db, kind: str, version: str) -> Optional[float]:
    rows = db.execute("SELECT at FROM mail_test_send WHERE kind = :k AND template_version = :v",
                      {"k": kind, "v": version}) if db is not None else []
    return float(rows[0][0]) if rows else None


def kinds(db, uid: str) -> list[dict]:
    rules = policies.read(uid)
    out = []
    for k in mailkinds.KINDS.values():
        row = {"kind": k.name, "title": k.title, "group": k.group,
               "switch": mailkinds.switch(rules, k.name), "default": k.default,
               "locked": k.locked, "editable": k.template is not None,
               "producer": k.producer or "none yet — a template ready for a flow to use"}
        if k.template is not None:
            raw, source = outbound._template(k, uid)
            version = mailtext.version(raw)
            row.update({"template": f"_global/mail/{k.template}.md", "source": source,
                        "template_version": version,
                        "test_sent": tested_version(db, k.name, version) is not None,
                        "variables": {**k.variables,
                                      **{f"{n} (always)": d
                                         for n, d in mailkinds.COMMON_VARIABLES.items()}},
                        "link": "Vexa appends one link at the end" if k.link else "no link"})
        out.append(row)
    return out


def validate_template(kind: str, content: str) -> str:
    k = mailkinds.get(kind)
    if k.template is None:
        raise MailAdminError(422, f"{kind} has no editable template (its text is fixed)")
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        raise MailAdminError(422, "the template is empty — write `subject: …`, `---`, then the body")
    if len(text) > TEMPLATE_MAX:
        raise MailAdminError(422, f"the template is longer than {TEMPLATE_MAX} characters")
    if not text.lower().startswith("subject:"):
        raise MailAdminError(422, "the first line must be `subject: <the subject line>`, then `---`")
    if _URL.search(text):
        raise MailAdminError(422, "a template never writes a URL — Vexa appends the one link itself")
    return text + "\n"


def edit(kind: str, content: str, *, uid: str, write_file: Callable) -> dict:
    text = validate_template(kind, content)
    k = mailkinds.get(kind)
    code, body = write_file(uid, f"mail/{k.template}.md", text, "_global")
    if code not in (200, 201):
        raise MailAdminError(403 if code == 403 else 502,
                             f"agent-api did not write _global/mail/{k.template}.md ({code}): "
                             f"{(body or {}).get('detail') if isinstance(body, dict) else body}")
    return {"kind": kind, "path": f"_global/mail/{k.template}.md",
            "template_version": mailtext.version(text),
            "next": "preview it (mail_preview), test-send it to yourself (mail_test_send), "
                    "then switch it on (mail_kind_set)"}


def test_send(db, kind: str, *, uid: str, email: str, sample: Optional[dict] = None,
              sender=None, now: Optional[float] = None) -> dict:
    """Send the CURRENT template of `kind`, with sample values, to the admin account's own address —
    the only recipient this verb has. Records the version as test-sent."""
    to = (email or "").strip()
    if "@" not in to:
        raise MailAdminError(422, "a test-send goes to your own address, and this account has none")
    k = mailkinds.get(kind)
    values = {**k.sample, **(sample or {})}
    link = None
    if k.link:
        try:
            from flows_steps.common import ui_link
            link = ui_link()
        except Exception:  # noqa: BLE001 — no UI URL configured: the test mail says so instead
            link = "(the link Vexa appends goes here)"
    pv = outbound.preview(kind, uid=uid, sample=sample) if k.template is not None else None
    out = outbound.send(kind, to, uid=uid, values=values,
                        subject=("[test] " + pv["subject"]) if pv else None, link=link,
                        db=db, test=True, sender=sender, now=now)
    if out.result != outbound.SENT:
        raise MailAdminError(409, f"the test-send did not go out: {out.result} — {out.detail}")
    db.execute("""INSERT INTO mail_test_send (kind, template_version, at, by)
                  VALUES (:k,:v,:a,:b) ON CONFLICT (kind, template_version) DO UPDATE SET at = :a""",
               {"k": kind, "v": out.template_version, "a": float(now or time.time()), "b": str(uid)})
    return {"kind": kind, "to": to, "template_version": out.template_version,
            "message_id": out.message_id, "delivery": out.delivery,
            **({"note": "this deployment CAPTURES mail: the test is in the capture double, not "
                        "in your inbox"} if out.delivery == "captured" else {}),
            "next": f"when it reads right, mail_kind_set(kind='{kind}', state='on')"}


def set_front_matter(raw: Optional[str], key: str, value: str) -> str:
    """`key: value` in the POLICIES front matter — replaced in place, else added before the closing
    fence; a file with no front matter gets one. The body is never touched."""
    text = raw or ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if close is not None:
            for i in range(1, close):
                k, sep, _ = lines[i].partition(":")
                if sep and k.strip().lower() == key:
                    lines[i] = f"{key}: {value}"
                    return "\n".join(lines) + "\n"
            lines.insert(close, f"{key}: {value}")
            return "\n".join(lines) + "\n"
    return f"---\n{key}: {value}\n---\n\n{text}"


def set_kind(db, kind: str, state: str, *, uid: str, read_file: Callable,
             write_file: Callable) -> dict:
    k = mailkinds.get(kind)
    state = (state or "").strip().lower()
    if state not in mailkinds.SWITCH_VALUES:
        raise MailAdminError(422, "state is on, off or admins")
    if k.locked and state != mailkinds.ON:
        raise MailAdminError(409, f"{kind} cannot be switched off — it is how people sign in; "
                                  f"its wording is yours to edit")
    version = outbound.template_version(kind, uid)
    if state != mailkinds.OFF and k.template is not None and tested_version(db, kind, version) is None:
        raise MailAdminError(409, f"{kind} is switched on only after its current template "
                                  f"(version {version}) has been test-sent — mail_test_send first")
    raw = read_file(uid, policies.POLICIES_FILE, "_global")
    new = set_front_matter(raw, mailkinds.policy_key(kind), state)
    code, body = write_file(uid, policies.POLICIES_FILE, new, "_global")
    if code not in (200, 201):
        raise MailAdminError(403 if code == 403 else 502,
                             f"agent-api did not write _global/POLICIES.md ({code}): "
                             f"{(body or {}).get('detail') if isinstance(body, dict) else body}")
    return {"kind": kind, "switch": state, "template_version": version}
