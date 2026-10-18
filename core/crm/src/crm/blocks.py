"""blocks.py — what a page's ``{{crm …}}`` block shows ONE viewer (PRD 0.13.4 D17, D20; UI 7cc).

A workspace page holds a placeholder (``{{crm company="OeNB"}}``, ``{{crm people company=OeNB}}``,
``{{crm deals stage=negotiation}}``, ``{{crm timeline person="Marvin Hinz" limit=5}}``). The page's
server renderer asks this, with the VIEWER's own key, and draws what comes back. Nothing from the
CRM is ever written into the workspace (D20: a copy is sharing; a reference is not).

Per field, the answer says whether the workspace may see it too (``shared``). A field the viewer
reads personally but the workspace may not is drawn with "only you see this" (§15). The workspace
side is the SAME rule a room reads by (``Store._view`` for a shared caller over the workspace, D4):
one ceiling, two readers.

Three things this never answers, so nothing leaks through layout (deny test 22):
* a record the viewer cannot read — it is absent, exactly as if it did not exist;
* a total, a count of matches or a "next page" marker — the rows are the answer;
* an error that differs between "hidden" and "nothing there" — both are an empty ``rows``.

Person only: a shared (room) caller is refused, because a block's "only you" half is personal and
a room's own reads go through ``crm_read`` / ``crm_search`` under its own ceiling.
"""
from __future__ import annotations

from sqlalchemy import select

from . import schema as s
from .caller import Caller, as_caller
from .store import CRMError, Forbidden, NotFound, SharedRefused

VIEWS = ('company', 'person', 'people', 'deals', 'timeline')
ROWS_MAX = 25
TITLE_FIELDS = ('Name', 'Title', 'Subject')


class BlockRefused(CRMError):
    """A shared caller asked for a personal render."""


def _title(fields: dict, kind: str) -> str:
    for k in TITLE_FIELDS:
        if fields.get(k):
            return str(fields[k])
    full = ' '.join(str(fields.get(k) or '') for k in ('FirstName', 'LastName')).strip()
    return full or kind


def _workspace_fields(store, c, org, viewer: Caller, workspace: str, row) -> set:
    """The fields the WORKSPACE may see on this record: the room ceiling for ``workspace`` (D4)."""
    if not workspace:
        return set()
    room = Caller(subject=viewer.subject, workspaces=(workspace,), writable=(), shared=True,
                  delegated=True, regime='human', org=viewer.org)
    try:
        policy = store._policy(c, org, room)
        view = store._view(policy, row)
    except (NotFound, SharedRefused, Forbidden):
        return set()
    return set(view.filter(row['fields']))


def _row_out(store, c, org, viewer, policy, workspace, row) -> dict:
    vis = store._visible(policy, row)
    shared = _workspace_fields(store, c, org, viewer, workspace, row)
    fields = [{'name': k, 'value': v, 'shared': k in shared}
              for k, v in sorted(vis['fields'].items()) if v not in (None, '', [], {})]
    return {'id': row['id'], 'type': row['object_type'], 'title': _title(vis['fields'], row['object_type']),
            'fields': fields, 'href': f"/crm?record={row['id']}"}


def _find(c, org, kind: str, name: str):
    """Records of ``kind`` whose Name is ``name`` (case-insensitive), newest id first."""
    want = str(name or '').strip().casefold()
    if not want:
        return []
    rows = c.execute(select(s.records).where(s.records.c.org_id == org, s.records.c.object_type == kind,
                                             s.records.c.deleted == False, s.records.c.merged_into.is_(None))  # noqa: E712
                     .order_by(s.records.c.id)).mappings().all()
    return [dict(r) for r in rows if _title(r['fields'] or {}, kind).casefold() == want]


def read_block(store, org: str, actor, workspace: str, view: str, args: dict) -> dict:
    """``{view, rows}`` for one ``{{crm …}}`` block, for this viewer."""
    viewer = as_caller(actor)
    if viewer.shared:
        raise BlockRefused('A page block is rendered for one person; a room reads with crm_read.')
    view = str(view or '').strip().lower()
    if view not in VIEWS:
        return {'view': view, 'rows': []}
    limit = max(1, min(int(str(args.get('limit') or ROWS_MAX)) if str(args.get('limit') or '').isdigit() else ROWS_MAX, ROWS_MAX))
    with store.engine.connect() as c:
        try:
            policy = store._policy(c, org, viewer)
        except Forbidden:
            return {'view': view, 'rows': []}
        readable = []

        def add(row):
            try:
                readable.append(_row_out(store, c, org, viewer, policy, workspace, row))
            except (NotFound, SharedRefused, Forbidden):
                pass                     # not this viewer's: absent, exactly like nothing

        if view in ('company', 'person'):
            kind = 'Company' if view == 'company' else 'Person'
            for row in _find(c, org, kind, args.get(view) or args.get('name') or ''):
                add(row)
                if readable:
                    break
        elif view in ('people', 'deals'):
            kind = 'Person' if view == 'people' else 'Deal'
            company = str(args.get('company') or '').strip()
            company_ids = set()
            if company:
                for row in _find(c, org, 'Company', company):
                    try:
                        store._view(policy, row)
                        company_ids.add(row['id'])
                    except (NotFound, SharedRefused, Forbidden):
                        continue
                if not company_ids:
                    return {'view': view, 'rows': []}
            stage = str(args.get('stage') or '').strip().casefold()
            rows = c.execute(select(s.records).where(s.records.c.org_id == org, s.records.c.object_type == kind,
                                                     s.records.c.deleted == False,  # noqa: E712
                                                     s.records.c.merged_into.is_(None))
                             .order_by(s.records.c.id)).mappings().all()
            for r in rows:
                row = dict(r)
                f = row['fields'] or {}
                if company_ids and f.get('Company') not in company_ids:
                    continue
                if stage and str(f.get('Stage') or '').casefold() != stage:
                    continue
                before = len(readable)
                add(row)
                if stage and len(readable) > before:
                    # a filter on a field the viewer cannot read would say which records hold it
                    names = {x['name'] for x in readable[-1]['fields']}
                    if 'Stage' not in names:
                        readable.pop()
                if len(readable) >= limit:
                    break
        elif view == 'timeline':
            who = args.get('person') or args.get('company') or ''
            kind = 'Person' if args.get('person') else 'Company'
            for row in _find(c, org, kind, who):
                try:
                    store._view(policy, row)
                except (NotFound, SharedRefused, Forbidden):
                    continue
                hist = store.history(org, viewer, row['id'])
                shared = _workspace_fields(store, c, org, viewer, workspace, row)
                for rev in (hist.get('revisions') or [])[:limit]:
                    after = (rev.get('after') or {}).get('fields') or {}
                    before = (rev.get('before') or {}).get('fields') or {}
                    changed = {k: v for k, v in after.items() if before.get(k) != v}
                    readable.append({'id': f"{row['id']}@{rev.get('revision')}", 'type': 'Revision',
                                     'title': str(rev.get('created_at') or ''),
                                     'fields': [{'name': k, 'value': v, 'shared': k in shared}
                                                for k, v in sorted(changed.items()) if v not in (None, '', [], {})],
                                     'href': f"/crm?record={row['id']}"})
                break
        return {'view': view, 'rows': readable[:limit]}
