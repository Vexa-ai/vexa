"""page_images.py — AN IMAGE ADDRESS AN AGENT DID NOT CHECK IS A GUESS (Vexa-ai/vexa#1624).

Founder, 2026-09-06, on the OeNB workspace README: the page carried
``![OeNB logo](https://upload.wikimedia.org/wikipedia/commons/8/8c/%C3%96NB_Logo.svg)``. Pressing
*Fetch into the workspace* answered, in red, that the address had returned **404**. Nobody had ever
requested it: the agent wrote a plausible Wikimedia path and moved on. A guessed URL is not a small
error — it is a picture the reader will never see, in a document written for a customer, wearing an
alt text that says exactly what the picture would have shown.

So the rule is the one `asset_source` already states one layer over, in a different direction: that
module says a page's picture must LIVE in the workspace; this one says an address written into a
page must ANSWER. Both are about the same failure — a reference to bytes nobody has ever held.

**THE CHECK IS CHEAP AND THE REFUSAL IS QUIET.** A HEAD (a ranged GET when the host will not take
one) with a short timeout, on each DISTINCT address, in parallel — so a page with eight pictures
costs one round trip, not eight. A page with no external image reference costs one regex and never
opens a socket, which is almost every write.

**WHAT HAPPENS TO A DEAD ONE: THE SENTENCE STAYS, THE IMAGE GOES.** The prose an agent wrote around
a picture is usually right — it was the address it invented. Dropping the paragraph would throw away
work to punish a bad link; leaving the reference would ship a broken picture into a customer's
workspace. So the reference alone is removed, the caller is told which addresses went and why, and
the caller files that as friction under the agent's own name — because the agent cannot report what
it never noticed, and this is exactly the class of defect PRD decision 33 exists to collect.

**THE OUTBOUND GUARD IS `asset_source.fetch_refusal`, NOT A SECOND ONE.** Checking an address is
still fetching an address: a page that said `<img src="http://169.254.169.254/…">` would otherwise
turn this verifier into the SSRF probe the fetch route refuses to be. Every redirect hop is
re-checked for the same reason `fetch_asset` re-checks its own.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, NamedTuple, Optional
from urllib.parse import urljoin

import httpx

from shared import asset_source as assets

#: ONE address's whole check. Deliberately much shorter than `asset_source.FETCH_TIMEOUT` (20s):
#: that one is a download the reader pressed a button for and is watching, this one is a gate in
#: front of a write nobody is waiting on with a picture in mind. A slow host costs the picture.
VERIFY_TIMEOUT = 5.0

#: How many addresses are checked at once. A page with a dozen pictures must cost one round trip's
#: wall clock and not twelve — the write it gates is a person's save or an agent's tool call.
VERIFY_PARALLEL = 8

#: Redirect hops followed while checking, each re-guarded. Same ceiling as the fetch itself.
MAX_HOPS = assets.MAX_REDIRECTS

_UA = {"User-Agent": "vexa-agent/0.12 (+https://vexa.ai)", "Accept": "image/*,*/*;q=0.8"}

#: `![alt](https://…)` and `<img … src="https://…">` — the two ways an image address reaches a page.
#: One regex with two branches so a single pass yields non-overlapping spans in document order.
#: Only `http(s)`: a workspace path is the shape we WANT, and a `data:` image carries its own bytes.
_MARKDOWN = r"!\[(?P<alt>[^\]\n]*)\]\(\s*(?P<url>https?://[^)\s]+?)\s*(?:\"[^\"]*\"|'[^']*')?\s*\)"
_HTML = r"<img\b[^>]*?\bsrc\s*=\s*(?P<q>[\"'])(?P<url2>https?://[^\"'>]+)(?P=q)[^>]*/?>"
_IMAGE_REF = re.compile(f"{_MARKDOWN}|{_HTML}", re.IGNORECASE)
_HTML_ALT = re.compile(r"\balt\s*=\s*([\"'])(?P<alt>[^\"']*)\1", re.IGNORECASE)


class ImageRef(NamedTuple):
    """One external image reference as it appears in a page."""
    url: str
    alt: str
    #: the exact text that would be removed — the whole `![…](…)` or `<img …>`
    text: str


def _ref(m: "re.Match[str]") -> ImageRef:
    url = m.group("url") or m.group("url2") or ""
    alt = m.group("alt")
    if alt is None:
        hit = _HTML_ALT.search(m.group(0))
        alt = hit.group("alt") if hit else ""
    return ImageRef(url=url.strip(), alt=(alt or "").strip(), text=m.group(0))


def external_image_refs(text: str) -> list[ImageRef]:
    """Every `http(s)` image reference in ``text``, in the order it appears.

    Exported because the interesting half of this module is what it FINDS: a caller that wants to
    warn rather than remove, and every test about the shapes an address arrives in, needs the list
    without the network."""
    return [_ref(m) for m in _IMAGE_REF.finditer(text or "")]


class _Answer(NamedTuple):
    """What one probe learned: a status and a media type, or the reason there is neither."""
    status: Optional[int]
    ctype: str
    refusal: Optional[str]


def _probe(cli: httpx.Client, method: str, url: str,
           resolve: Optional[Callable[[str], list[str]]]) -> _Answer:
    """One request, following redirects BY HAND so every hop is re-guarded.

    `httpx`'s own `follow_redirects` would send the second request without asking us about it, and
    a redirect into link-local space is the classic way past a guard that only reads the URL it was
    handed — `asset_source.fetch_asset` walks its hops for the same reason."""
    headers = dict(_UA) if method == "HEAD" else {**_UA, "Range": "bytes=0-0"}
    target = url
    for _hop in range(MAX_HOPS + 1):
        guard = assets.fetch_refusal(target, resolve)
        if guard:
            return _Answer(None, "", guard)
        try:
            r = cli.request(method, target, headers=headers, timeout=VERIFY_TIMEOUT)
        except httpx.HTTPError as exc:
            return _Answer(None, "", f"could not reach {url}: {type(exc).__name__}")
        location = r.headers.get("location")
        if r.status_code in (301, 302, 303, 307, 308) and location:
            target = urljoin(str(r.request.url), location)
            continue
        return _Answer(r.status_code,
                       (r.headers.get("content-type") or "").split(";")[0].strip().lower(), None)
    return _Answer(None, "", f"{url} redirects more than {MAX_HOPS} times")


def image_refusal(url: str, *, client: Optional[httpx.Client] = None,
                  resolve: Optional[Callable[[str], list[str]]] = None) -> Optional[str]:
    """``None`` when this address answers with an image, else the reason it may not be written.

    A HEAD first — the cheapest question available, and the one a CDN answers without sending a
    file. Some hosts refuse the METHOD rather than the resource (405/501, and a surprising number of
    403s) and some answer it with no type at all; in either case the fall-back is a one-byte ranged
    GET, which is a real read of the real resource and is what settles it.

    The verdict is about the RESOURCE, not about our luck: a 2xx that is not an image is refused
    with the type it did send, because an `<img>` pointed at an HTML error page renders as a broken
    picture exactly like a 404 does."""
    refusal = assets.fetch_refusal(url, resolve)
    if refusal:
        return refusal
    own = client is None
    cli = client or httpx.Client(timeout=VERIFY_TIMEOUT, follow_redirects=False)
    try:
        a = _probe(cli, "HEAD", url, resolve)
        # the host answered about the METHOD, or said nothing about the file — ask again properly
        if a.status is not None and (a.status in (403, 405, 501) or (a.status < 400 and not a.ctype)):
            a = _probe(cli, "GET", url, resolve)
        if a.refusal:
            return a.refusal
        if a.status is None or a.status >= 400:
            return f"{url} answered {a.status}"
        if not a.ctype:
            return f"{url} answered {a.status} but did not say what it is"
        if not a.ctype.startswith("image/"):
            return f"{url} answered {a.ctype}, which is not an image"
        return None
    finally:
        if own:
            cli.close()


class Dropped(NamedTuple):
    """One image reference that was removed, and why — what the caller reports as friction."""
    url: str
    alt: str
    reason: str


def _verdicts(urls: Iterable[str], verify: Callable[[str], Optional[str]]) -> dict[str, Optional[str]]:
    """Check each DISTINCT address once, and all of them at once.

    Sequentially, a page whose eight pictures all point at a host that is merely slow would hold a
    write open for `8 × VERIFY_TIMEOUT`. The bound that matters to the person saving the page is
    wall clock, so the pool is what makes the ceiling real."""
    todo = sorted({u for u in urls if u})
    if not todo:
        return {}
    if len(todo) == 1:
        return {todo[0]: verify(todo[0])}
    with ThreadPoolExecutor(max_workers=min(VERIFY_PARALLEL, len(todo))) as pool:
        return dict(zip(todo, pool.map(verify, todo)))


def _tidy(text: str) -> str:
    """Close the hole a removed reference leaves — and nothing else.

    A picture on a line of its own leaves a blank line between two blank lines; a picture inside a
    sentence leaves a double space. Both are invisible to a reader and neither is worth a rewrite of
    the page, so this is the whole repair: it never reflows, re-wraps or re-orders anything."""
    out = re.sub(r"[ \t]+\n", "\n", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"\n{3,}", "\n\n", out)


def screen_text(text: str, *, verify: Optional[Callable[[str], Optional[str]]] = None,
                ) -> tuple[str, list[Dropped]]:
    """``(text without the dead image references, what was dropped)``.

    The default `verify` opens sockets, so every test — and every caller that already knows an
    answer — passes its own. A text with no external image reference returns unchanged, by the
    first line and without a client."""
    src = text or ""
    refs = external_image_refs(src)
    if not refs:
        return src, []
    verdicts = _verdicts((r.url for r in refs), verify or image_refusal)
    dropped: list[Dropped] = []

    def _cut(m: "re.Match[str]") -> str:
        ref = _ref(m)
        reason = verdicts.get(ref.url)
        if not reason:
            return m.group(0)
        # THE SENTENCE STAYS. Only the reference goes — the prose around it was not the mistake.
        dropped.append(Dropped(url=ref.url, alt=ref.alt, reason=reason))
        return ""

    out = _IMAGE_REF.sub(_cut, src)
    return (_tidy(out), dropped) if dropped else (src, [])


def screen_values(values: Iterable[str], *, verify: Optional[Callable[[str], Optional[str]]] = None,
                  ) -> tuple[list[str], list[Dropped]]:
    """`screen_text` over several fields of ONE write — a card's summary, its facts, its questions.

    One call rather than a loop of them so the addresses across all the fields are checked together:
    a page whose summary and whose timeline name the same dead logo asks the host once."""
    vals = [v if isinstance(v, str) else str(v or "") for v in values]
    joined = external_image_refs("\n".join(vals))
    if not joined:
        return vals, []
    verdicts = _verdicts((r.url for r in joined), verify or image_refusal)
    out, dropped = [], []
    for v in vals:
        clean, gone = screen_text(v, verify=lambda u: verdicts.get(u))
        out.append(clean)
        dropped.extend(gone)
    return out, dropped


def friction_report(dropped: list[Dropped], *, path: str = "", tool: str = "") -> dict:
    """The rough-edges record for a caught guess (PRD decision 33), ready for `shared.friction`.

    It is filed under the AGENT, because the agent is who did it and `report_friction` is the
    channel a developer reads — but the agent cannot file this one itself: it never learned the
    address was dead. That is the whole argument for the check being here rather than a rule in a
    prompt. The URL is named in full: the next reader's first question is which address, and a
    report that says "an image" answers nothing."""
    named = "; ".join(f"{d.url} ({d.reason})" for d in dropped[:5])
    where = f" into {path}" if path else ""
    return {
        "reporter": "agent",
        "kind": "error",
        "severity": "annoyance",
        "tried": f"wrote {len(dropped)} external image reference(s){where}: {named}",
        "happened": ("the address was never checked before it was written, and it does not answer "
                     "with an image — the reference was dropped from the page and the text kept"),
        "would_help": ("check an image address before writing it (HEAD it, or fetch it into "
                       "assets/), and write the sentence without the picture when it does not "
                       "answer"),
        "context": {k: v for k, v in (("path", path), ("tool", tool)) if v},
    }
