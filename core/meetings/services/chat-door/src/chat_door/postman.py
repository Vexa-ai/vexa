"""The postman — a rendered artifact becomes an email carrying the door.

```
uv run python -m chat_door.postman \
  --artifact fixtures/artifact-loop/rendered/11706/alexey-rogov.md \
  --to someone@example.test \
  --base-url http://127.0.0.1:8080 \
  --smtp-host 127.0.0.1 --smtp-port 11025
```

What it does, in order: read the artifact → mint a **magic link** for that email identity,
scoped to that artifact's record → rewrite the placeholder record link to it → build a
``multipart/alternative`` message (markdown as ``text/plain``, a light rendering as
``text/html``) → hand it to SMTP.

Two deliberate choices:

* **The link is never printed** unless ``--print-link`` is passed. It is a bearer capability
  for one person's record; the demo path is to open it in Mailpit, not to read it off a
  terminal. The signing key is never printed at all — only its fingerprint.
* **``--dry-run`` writes the exact ``.eml``** it would have sent, so the MIME can be inspected
  (and asserted in tests) with no SMTP in the loop.
"""
from __future__ import annotations

import argparse
import re
import smtplib
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from pathlib import Path

from .artifact import Artifact, load_artifact, with_record_link
from .config import load_config
from .tokens import TokenSigner, build_magic_link

DEFAULT_FROM = "Vexa <artifacts@vexa.local>"


@dataclass(frozen=True)
class BuiltMessage:
    message: EmailMessage
    link: str
    rewrote_link: bool


def build_message(
    artifact: Artifact,
    *,
    to_email: str,
    link: str,
    from_addr: str = DEFAULT_FROM,
    message_id: str | None = None,
) -> BuiltMessage:
    body, rewrote = with_record_link(artifact.body, link)
    if not rewrote:
        body = f"{body.rstrip()}\n\n[open the record]({link})\n"

    msg = EmailMessage()
    msg["Subject"] = artifact.subject
    msg["From"] = from_addr
    msg["To"] = formataddr((artifact.recipient_name, to_email)) if artifact.recipient_name else to_email
    msg["Message-ID"] = message_id or make_msgid(domain="vexa.local")
    # A consumer (and a human reading raw headers in Mailpit) can see which record this is
    # about without parsing the body.
    msg["X-Vexa-Record"] = str(artifact.meeting_id)
    msg["X-Vexa-Artifact"] = artifact.participant_slug
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body, subtype="plain", charset="utf-8")
    msg.add_alternative(_to_html(body, title=artifact.subject), subtype="html", charset="utf-8")
    return BuiltMessage(message=msg, link=link, rewrote_link=rewrote)


def _to_html(markdown: str, *, title: str) -> str:
    """A deliberately small markdown rendering — the artifact uses six constructs."""
    out: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.strip() in ("---", "***"):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<hr>")
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line.strip():
            continue
        if line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 4)
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title></head>"
        "<body style=\"font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:40rem;margin:0 auto;padding:1.5rem;color:#1d1d1f\">"
        + "".join(out)
        + "</body></html>"
    )


def _inline(text: str) -> str:
    html = escape(text)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", html)
    return html


def send(
    message: EmailMessage, *, host: str, port: int, timeout: float = 15.0
) -> None:
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.send_message(message)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m chat_door.postman",
        description="Send one rendered artifact as an email whose record link is a magic link.",
    )
    ap.add_argument("--artifact", required=True, type=Path, help="path to <meeting>/<participant>.md")
    ap.add_argument("--to", required=True, help="recipient email — also the door identity")
    ap.add_argument("--from-addr", default=DEFAULT_FROM)
    ap.add_argument("--scope", default="guest", choices=("guest", "member"))
    ap.add_argument("--base-url", default=None, help="door origin (default: CHAT_DOOR_BASE_URL)")
    ap.add_argument("--smtp-host", default="127.0.0.1")
    ap.add_argument("--smtp-port", type=int, default=1025)
    ap.add_argument("--dry-run", type=Path, nargs="?", const=Path("-"),
                    help="write the .eml instead of sending (default: stdout)")
    ap.add_argument("--print-link", action="store_true",
                    help="echo the magic link (a bearer capability — off by default)")
    args = ap.parse_args(argv)

    config = load_config()
    base_url = args.base_url or config.base_url
    artifact = load_artifact(args.artifact)
    signer = TokenSigner(bytes(config.signing_key))
    token = signer.issue(
        kind="link",
        subject=args.to,
        meeting_id=artifact.meeting_id,
        scope=args.scope,
        ttl_seconds=config.link_ttl_seconds,
    )
    built = build_message(
        artifact, to_email=args.to, link=build_magic_link(base_url, token),
        from_addr=args.from_addr,
    )

    if args.dry_run is not None:
        raw = built.message.as_string()
        if str(args.dry_run) == "-":
            sys.stdout.write(raw)
        else:
            args.dry_run.write_text(raw, "utf-8")
            print(f"wrote {args.dry_run}")
    else:
        send(built.message, host=args.smtp_host, port=args.smtp_port)
        print(f"sent → {args.to} · record {artifact.meeting_id} · subject {artifact.subject!r}")

    if config.signing_key.generated:
        print(
            "warning: no CHAT_DOOR_SIGNING_KEY set — an ephemeral key was generated, so this "
            "link only verifies against a door process holding the same key. "
            f"key fingerprint {config.signing_key.fingerprint}",
            file=sys.stderr,
        )
    if args.print_link:
        print(built.link)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
