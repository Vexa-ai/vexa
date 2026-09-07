"""flow_pages_watch.py — THE PAGE OF A FLOW SOMEBODY WROTE, LANDED IN `_global/flows/`.

Founder, 2026-09-06, in the governance chat of `_global`: *"we want to be able to write flows for
the global chat as we like."* (Vexa-ai/vexa#1639.) `flows_submit` files a flow as data and the
worker runs it about ten seconds later; the admin then has nothing to look at. The image's flows
each have a generated page in `_global/flows/` (#1615/#1626) — trigger, the steps in order with
what each reads, does and leaves behind, what it mails, the rules it honours, the Python at the
foot. A flow the admin authored had none.

flows-api renders those pages (`GET /flows/pages`, one per runtime-authored VERSION). This module
is what puts them on disk, and the split is not a preference:

  * **flows-api has no `_global`.** `deploy/compose/docker-compose.yml` gives that service no
    volumes at all. agent-api holds the organisation tier, mounted twice and writable in the store
    copy (`control_plane/api._global_store`), and already seeds files into it at boot
    (`preset_library.top_up`, `global_seed.top_up`). The only way a hook inside flows-api could
    write the page is by calling agent-api — a new inbound write door and a new credential on the
    one service that already owns the directory, to invert an arrow that today points outward
    (agent-api publishes facts INTO flows and reads it over HTTP; flows calls nothing here).
  * **A hook fires once; a page is a promise the product keeps continuously.** When the one write
    cannot land — the host-path mirror is bound `:ro`, agent-api is mid-restart, the directory does
    not exist yet — the flow is live with no page and nothing retries. This is a reconciler: it
    converges, at boot and every `POLL_S`, which is the same rule the two seed top-ups already
    follow one directory down.
  * **It is inside the ten seconds either way.** `flows_submit` promises `live_within_s: 10`; a
    five-second poll plus one small request lands the page while the worker is still picking the
    row up.

WHAT IT MAY WRITE, AND NOTHING ELSE. `_global/flows/` holds two sets: the seeded `<flow>.md` pages
of the image's own flows, which an admin may edit and which `global_seed.top_up` will never
overwrite, and `<flow>@<version>.md` — one per version somebody authored. This module writes ONLY
the second shape (`RUNTIME_PAGE`), so the two writers cannot reach each other's files. It never
deletes: `flow_version` rows are retired, never removed, so a page that exists still describes
something that ran, and a retired version's page says so in its own first line.

IT COMPARES BYTES BEFORE IT WRITES. The poll carries an etag per page so the hot loop does not
carry every step's source across every five seconds (`post_meeting`'s page is fifty kilobytes), and
the etag is recomputed here from the file on disk. If the two ever computed it differently the cost
is a wasted fetch and never a wasted write — `_global` is a git repository and a writer that
rewrote identical bytes every five seconds would fill it with commits saying nothing.

NEVER RAISES, at any depth. A page that could not be written is reported by the return value and
the log; a flow with no page is a worse product and a service that will not boot is a broken one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from control_plane import publish as publish_mod

logger = logging.getLogger("agent_api.flow_pages_watch")

#: The directory under `_global` these pages live in. flows-api answers the same name on its own
#: route (`{"dir": ...}`, from `flows_pages.PAGES_DIR[-1]`), and `test_flow_pages_watch.py` pins
#: this constant against what the route says rather than restating the path in prose.
FLOWS_DIRNAME = "flows"

#: THE ONLY FILENAMES THIS MODULE OWNS — `<flow>@<version>.md`. The seeded image pages are
#: `<flow>.md` and are the seed's; a file that matches neither belongs to whoever put it there.
#:
#: THE FIRST CHARACTER IS ALPHANUMERIC, and that is not cosmetic: `flows_submit` accepts any 80
#: characters as a flow NAME, so the filename on the wire is derived from a value a caller chose.
#: `..@1.md` is a legal filename and a legal flow name, and a directory with one in it is a
#: directory somebody is probing. The name is a string from another service either way — never a
#: path — so it is matched, not sanitised.
RUNTIME_PAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}@\d{1,9}\.md$")

#: How often the directory is reconciled. Five seconds against `flows_submit`'s ten-second promise:
#: the page is there by the time the flow is.
POLL_S = 5.0

#: Bounded on purpose — this runs on a daemon thread beside a service people are using, and flows
#: being slow must cost this loop a cycle, never the process.
TIMEOUT_S = 4.0


@dataclass(frozen=True)
class FlowPagesWatchHandle:
    """Background watcher control handle — the shape `RoutineReconcilerHandle` already has."""

    thread: threading.Thread
    stop_event: threading.Event

    def stop(self) -> None:
        self.stop_event.set()


def etag(body: "str | bytes") -> str:
    """The content hash flows-api sends beside each page.

    ONE LINE, WRITTEN TWICE, ON PURPOSE — `flows_pages.etag` is the other copy, and the two live in
    two images that never import each other. A shared constant would need a shared package; a
    disagreement between them costs one redundant fetch per cycle and no wrong page, because
    `reconcile` compares the fetched bytes with the file before it writes."""
    raw = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    return hashlib.sha256(raw).hexdigest()[:16]


def _get(path: str) -> Optional[dict]:
    """One GET onto flows-api with the operator key. `None` for every failure, including "no flows
    domain here" — a deployment that runs no flows has no runtime flows and therefore no pages."""
    base = publish_mod._flows_base()      # ONE reader of VEXA_FLOWS_API_URL/_KEY in this service
    key = publish_mod._flows_key()
    if not base or not key:
        return None
    req = urllib.request.Request(f"{base}{path}", headers={"X-Flows-Operator-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
            if not 200 <= r.status < 300:
                return None
            return json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001 — see the module docstring: this never fails a caller
        return None


def index() -> list[dict]:
    """`[{file, flow, version, status, etag}]` — the poll shape, no page bodies."""
    body = _get("/flows/pages?bodies=0")
    pages = (body or {}).get("pages")
    return [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []


def bodies(files: "list[str]") -> list[dict]:
    """The named pages, with their markdown. Empty list when nothing was asked for."""
    if not files:
        return []
    body = _get("/flows/pages?" + urllib.parse.urlencode({"only": ",".join(sorted(files))}))
    pages = (body or {}).get("pages")
    return [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []


def on_disk(pages_dir: "str | Path") -> dict:
    """`{filename: etag}` for the runtime pages already there. Nothing else in the directory is
    read: the seeded `<flow>.md` pages are not this writer's and are not its business."""
    out: dict = {}
    d = Path(pages_dir)
    if not d.is_dir():
        return out
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return out
    for f in entries:
        if not f.is_file() or not RUNTIME_PAGE.match(f.name):
            continue
        try:
            out[f.name] = etag(f.read_bytes())
        except OSError:
            continue
    return out


def stale(rows: "list[dict]", have: dict) -> list[str]:
    """Which pages have to be fetched: missing here, or here with a different content hash.

    A row whose filename is not one this module may write is DROPPED rather than fetched — the
    filename comes from another service, and "write whatever it names" is how a path traversal is
    written by accident."""
    want: list[str] = []
    for row in rows:
        name = str(row.get("file") or "")
        if not RUNTIME_PAGE.match(name):
            if name:
                logger.warning("flow pages: refusing the filename %r — this writer owns "
                               "<flow>@<version>.md and nothing else", name)
            continue
        if have.get(name) != str(row.get("etag") or ""):
            want.append(name)
    return want


def reconcile(global_root: "str | Path",
              *, index_fn: "Callable[[], list[dict]] | None" = None,
              bodies_fn: "Callable[[list[str]], list[dict]] | None" = None) -> list[str]:
    """One pass. Returns the filenames actually written, in order. NEVER raises.

    The two functions are arguments so the loop can be driven in a test without a socket — the same
    reason `global_seed.top_up` takes its sources."""
    rows = (index_fn or index)()
    if not rows:
        return []
    pages_dir = Path(global_root) / FLOWS_DIRNAME
    want = stale(rows, on_disk(pages_dir))
    if not want:
        return []
    try:
        pages_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("flow pages: cannot create %s (%s) — a flow authored from the chat has no "
                       "page here until this directory is writable", pages_dir, e)
        return []
    written: list[str] = []
    for page in (bodies_fn or bodies)(want):
        name = str(page.get("file") or "")
        body = page.get("body")
        if not RUNTIME_PAGE.match(name) or not isinstance(body, str) or not body:
            continue
        target = pages_dir / name
        raw = body.encode("utf-8")
        try:
            # BYTES BEFORE WRITE — see the module docstring. `_global` is a git repository, and a
            # writer that rewrote identical content every cycle would fill its history with commits
            # that say nothing happened.
            if target.exists() and target.read_bytes() == raw:
                continue
            target.write_bytes(raw)
        except OSError as e:
            logger.warning("flow pages: could not write %s (%s)", target, e)
            continue
        written.append(name)
        logger.info("flow pages: wrote %s — the flow %s@%s, authored through the API",
                    target, page.get("flow"), page.get("version"))
    return written


def start(global_root: "str | Path", *, interval_sec: float = POLL_S
          ) -> Optional[FlowPagesWatchHandle]:
    """Reconcile once now, then keep the directory in step on a daemon thread.

    `interval_sec <= 0` runs nothing and returns None — the shape
    `start_workspace_routine_reconciler` already uses, so a deployment turns this off the same way
    it turns that off."""
    if interval_sec <= 0:
        return None

    def once() -> None:
        try:
            written = reconcile(global_root)
            if written:
                logger.info("flow pages: %d page(s) reconciled into %s — %s",
                            len(written), global_root, ", ".join(written))
        except Exception:  # noqa: BLE001 — a daemon thread that raises stops reconciling forever
            logger.exception("flow pages: reconcile failed")

    once()
    stop_event = threading.Event()

    def loop() -> None:
        while not stop_event.wait(interval_sec):
            once()

    thread = threading.Thread(target=loop, name="flow-pages-watch", daemon=True)
    thread.start()
    return FlowPagesWatchHandle(thread=thread, stop_event=stop_event)
