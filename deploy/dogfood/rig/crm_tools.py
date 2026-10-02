"""Optional CRM tools for the control MCP; identity and delegation stay with the host."""
import json
from typing import Literal


def register_crm_tools(mcp, *, base_url, subject, scope, user_key, http, guard):
    if not base_url:
        return

    def call(operation, tenant_id, **body):
        actor = subject()
        ceiling = scope()
        if ceiling is not None:
            # Workspace grants do not implicitly grant access to a separate CRM tenant.
            human = ceiling.get('regime') == 'human' and ceiling.get('workspaces') == '*'
            tenants = ceiling.get('crm_tenants', [])
            if not human and not (isinstance(tenants, list) and tenant_id in tenants):
                return json.dumps({'refused': 'out_of_scope', 'detail': 'This delegation does not include this CRM tenant.'})
        status, result = http('POST', base_url.rstrip('/') + '/' + operation,
                              headers={'X-API-Key': user_key(actor)},
                              body={'tenant_id': tenant_id, **body})
        if status == 0:
            return json.dumps({'status': 503, 'detail': 'CRM is temporarily unavailable'})
        return json.dumps({'status': status, 'result': result})

    @mcp.tool()
    @guard
    def crm_describe(tenant_id: str, object_type: str = '') -> str:
        """Discover permitted CRM object types and fields before searching or writing. CRM account data belongs here; reusable strategy belongs in workspaces."""
        return call('describe', tenant_id, object_type=object_type)

    @mcp.tool()
    @guard
    def crm_search(tenant_id: str, object_type: str, filters: dict | None = None,
                   limit: int = 20, offset: int = 0) -> str:
        """Find permitted CRM records using equality filters on readable fields. Returns stable IDs and revisions; paginate with offset."""
        return call('search', tenant_id, object_type=object_type, filters=filters or {}, limit=limit, offset=offset)

    @mcp.tool()
    @guard
    def crm_read(tenant_id: str, record_id: str) -> str:
        """Read permitted fields, sources, links, proposals and revision. Top-level id is the native UUID; fields.Id is the imported source ID. Use the returned href for the native Minutes record link. Do not copy CRM account data into workspace files."""
        return call('read', tenant_id, record_id=record_id)

    @mcp.tool()
    @guard
    def crm_change(tenant_id: str, action: Literal['create', 'update', 'propose'], reason: str,
                   object_type: str = '', record_id: str = '', expected_revision: int = 0,
                   fields: dict | None = None, narrative: str | None = None,
                   evidence: list[dict] | None = None, idempotency_key: str = "") -> str:
        """Create, update or propose a CRM change with a reason and evidence. Create requires a stable idempotency_key for safe retries. Read first and pass expected_revision; proposals require review and are not saved edits."""
        return call('change', tenant_id, action=action, reason=reason, object_type=object_type,
                    record_id=record_id, expected_revision=expected_revision, fields=fields or {},
                    narrative=narrative, evidence=evidence or [], idempotency_key=idempotency_key)

    @mcp.tool()
    @guard
    def crm_history(tenant_id: str, record_id: str) -> str:
        """Inspect recent immutable CRM revisions, filtered by current field permissions."""
        return call('history', tenant_id, record_id=record_id)

    @mcp.tool()
    @guard
    def crm_review(tenant_id: str, proposal_id: str, accept: bool) -> str:
        """Accept or reject a pending CRM proposal with reviewer rights. Stale proposals conflict; acceptance creates a new revision."""
        return call('review', tenant_id, proposal_id=proposal_id, accept=accept)
