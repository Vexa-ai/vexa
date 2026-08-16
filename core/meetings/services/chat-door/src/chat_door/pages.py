"""The door's HTML — three small pages, no assets, no framework, no external requests.

Every page carries the same header banner stating what this is: **a dev v0**. That is not
decoration. The spec's honesty valve says a delta over a bad transcript must be inspectable;
the same principle applied to the door itself means the door does not pretend to be the
finished product while it is a week-one stub. Concretely: the reply box says the reply is
stored and will shape the next artifact — it does not simulate a conversation, because there
is no model behind it yet.
"""
from __future__ import annotations

from html import escape

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:#fbfbfa; color:#1d1d1f; }
@media (prefers-color-scheme: dark) { body { background:#131315; color:#e9e9ea; } }
main { max-width: 44rem; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
.banner { background:#fff4d6; color:#5c4300; border-bottom:1px solid #e8d089;
          padding:.55rem 1.25rem; font-size:.82rem; }
@media (prefers-color-scheme: dark) { .banner { background:#3a2f10; color:#f0dfae; border-color:#5c4a17; } }
h1 { font-size:1.4rem; margin:1.25rem 0 .25rem; }
h2 { font-size:1rem; margin:2rem 0 .5rem; text-transform:uppercase; letter-spacing:.06em;
     opacity:.6; font-weight:600; }
.meta { opacity:.65; font-size:.9rem; margin:0 0 .5rem; }
.note { border-left:3px solid #d9a441; padding:.5rem .85rem; background:rgba(217,164,65,.09);
        margin:1rem 0; font-size:.92rem; }
.seg { padding:.35rem 0; border-bottom:1px solid rgba(128,128,128,.16); }
.seg .who { font-weight:600; font-size:.85rem; opacity:.8; }
.seg .txt { white-space:pre-wrap; }
textarea { width:100%; min-height:7rem; padding:.7rem; border-radius:8px; font:inherit;
           border:1px solid rgba(128,128,128,.4); background:transparent; color:inherit; }
button { margin-top:.6rem; padding:.55rem 1.1rem; border-radius:8px; border:0; font:inherit;
         background:#1d1d1f; color:#fff; cursor:pointer; }
@media (prefers-color-scheme: dark) { button { background:#e9e9ea; color:#131315; } }
.err { border-left:3px solid #c4443a; padding:.6rem .85rem; background:rgba(196,68,58,.09); }
code { font-size:.88em; }
a { color: inherit; }
"""

_BANNER = (
    "Vexa chat door — <strong>dev v0</strong>. This page shows the meeting record and stores "
    "what you write into your personal instructions. There is no chat model behind it yet."
)


def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<div class=\"banner\">{_BANNER}</div><main>{body}</main></body></html>"
    )


def error_page(headline: str, detail: str, reason: str = "") -> str:
    reason_html = f"<p class=\"meta\">reason: <code>{escape(reason)}</code></p>" if reason else ""
    return _shell(
        headline,
        f"<h1>{escape(headline)}</h1><div class=\"err\">{escape(detail)}</div>{reason_html}",
    )


def record_page(
    *,
    subject: str,
    scope: str,
    record,
    steer_ack: str = "",
    instructions_excerpt: str = "",
) -> str:
    head = [
        f"<h1>{escape(record.title)}</h1>",
        f"<p class=\"meta\">record <code>{escape(str(record.meeting_id))}</code> · "
        f"{escape(record.platform)} · you are <code>{escape(subject)}</code> "
        f"(scope <code>{escape(scope)}</code>)</p>",
    ]
    if not record.found:
        head.append(f"<div class=\"note\">{escape(record.note)}</div>")
    elif not record.transcript_available:
        head.append(
            "<div class=\"note\">No transcript is readable for this record — "
            f"{escape(record.note)}. The record exists; its transcript does not answer.</div>"
        )
    elif not record.segments:
        head.append(
            "<div class=\"note\">This record's transcript resource is present and "
            "<strong>empty</strong> — that is different from a fetch that failed.</div>"
        )
    elif record.note:
        # A record that arrived by an unusual route says so — a demo must never quietly
        # substitute a file for the system under test.
        head.append(f"<div class=\"note\">{escape(record.note)}</div>")

    segs = []
    for s in record.segments[:400]:
        who = escape(str(s.get("speaker") or "unattributed"))
        txt = escape(str(s.get("text") or "").strip())
        if txt:
            segs.append(f"<div class=\"seg\"><div class=\"who\">{who}</div>"
                        f"<div class=\"txt\">{txt}</div></div>")
    if len(record.segments) > 400:
        segs.append(f"<p class=\"meta\">… {len(record.segments) - 400} more segments not shown.</p>")

    ack = ""
    if steer_ack:
        ack = (
            "<div class=\"note\"><strong>Saved.</strong> This will shape your next artifact."
            f"<br><span class=\"meta\">{escape(steer_ack)}</span></div>"
        )

    excerpt = ""
    if instructions_excerpt.strip():
        excerpt = (
            "<h2>Your instructions so far</h2>"
            f"<pre class=\"seg\"><code>{escape(instructions_excerpt)}</code></pre>"
        )

    form = (
        "<h2>Steer your next artifact</h2>"
        f"{ack}"
        f"<form method=\"post\" action=\"/door/steer\">"
        f"<input type=\"hidden\" name=\"meeting_id\" value=\"{escape(str(record.meeting_id))}\">"
        "<textarea name=\"text\" required placeholder=\"e.g. next time, focus on decisions, "
        "not descriptions\"></textarea>"
        "<div><button type=\"submit\">Save this</button></div></form>"
    )

    return _shell(
        record.title,
        "".join(head) + "<h2>The record</h2>" + ("".join(segs) or
        "<p class=\"meta\">Nothing to show.</p>") + form + excerpt,
    )
