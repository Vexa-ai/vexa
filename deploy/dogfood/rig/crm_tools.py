"""Optional CRM tools for the control MCP; identity and delegation stay with the host."""
import json
import os
from typing import Literal


def register_crm_tools(mcp, *, base_url, subject, scope, user_key, http, guard, tenant_id=None):
    if not base_url:
        return

    tenant_id = (tenant_id or os.environ.get("CRM_TENANT_ID", "")).strip()
    if not tenant_id:
        raise ValueError("CRM_TENANT_ID is required when CRM is enabled")

    def call(operation, **body):
        actor = subject()
        ceiling = scope()
        if ceiling is not None:
            # Workspace grants do not implicitly grant access to a separate CRM tenant.
            human = ceiling.get('regime') == 'human' and ceiling.get('workspaces') == '*'
            tenants = ceiling.get('crm_tenants', [])
            if not human and not (isinstance(tenants, list) and tenant_id in tenants):
                return json.dumps({'refused': 'out_of_scope', 'detail': 'This delegation does not include this CRM tenant.'})
        status, result = http('POST', base_url.rstrip('/') + '/' + operation,
                              headers={'X-API-Key': user_key(actor), 'X-CRM-Tenant': tenant_id},
                              body=body)
        if status == 0:
            return json.dumps({'status': 503, 'detail': 'CRM is temporarily unavailable'})
        return json.dumps({'status': status, 'result': result})

    @mcp.tool()
    @guard
    def crm_describe(object_type: str = '') -> str:
        """Discover permitted CRM object types and fields before searching or writing. This instance has one configured CRM; never ask the user for a tenant. CRM account data belongs here; reusable strategy belongs in workspaces."""
        return call('describe', object_type=object_type)

    @mcp.tool()
    @guard
    def crm_search(object_type: str, filters: dict | None = None,
                   limit: int = 20, offset: int = 0) -> str:
        """Find permitted CRM records using equality filters on readable fields. Returns stable IDs, revisions and href; paginate with offset. Use each record href for entity links instead of workspace wikilinks. Present the top-level href as a Markdown link to open the live CRM table in the Minutes sidebar. Table columns follow the shared crm_configure layout."""
        return call('search', object_type=object_type, filters=filters or {}, limit=limit, offset=offset)

    @mcp.tool()
    @guard
    def crm_read(record_id: str) -> str:
        """Read permitted fields, sources, links, proposals and revision. Top-level id is the native UUID; fields.Id is the imported source ID. Use the returned href for the native Minutes record link. Do not copy CRM account data into workspace files."""
        return call('read', record_id=record_id)

    @mcp.tool()
    @guard
    def crm_change(action: Literal['create', 'update', 'propose'], reason: str,
                   object_type: str = '', record_id: str = '', expected_revision: int = 0,
                   fields: dict | None = None, narrative: str | None = None,
                   evidence: list[dict] | None = None, idempotency_key: str = "") -> str:
        """Create, update or propose a CRM change with a reason and evidence. narrative is the native Markdown Description: headings, tables, [[Entity names]], workspace links and stable [label](/crm?record=UUID) links. Stable CRM links create graph relationships and backlinks. Author connected descriptions: search for mentioned people, companies and products, then link verified matches using their returned CRM href. Reuse workspace document links for shared knowledge. Within authorized entity-creation work, create missing entity records from evidence and link them; do not invent identities, facts or URLs. Preserve ambiguity rather than choosing a similar name. Use [[Entity names]] for workspace-style unresolved references. Bold names alone are not links. Omit narrative to preserve it; empty string clears it. Create requires a stable idempotency_key for safe retries. Read first and pass expected_revision; proposals require review and are not saved edits."""
        return call('change', action=action, reason=reason, object_type=object_type,
                    record_id=record_id, expected_revision=expected_revision, fields=fields or {},
                    narrative=narrative, evidence=evidence or [], idempotency_key=idempotency_key)

    @mcp.tool()
    @guard
    def crm_history(record_id: str) -> str:
        """Inspect recent immutable CRM revisions, filtered by current field permissions."""
        return call('history', record_id=record_id)

    @mcp.tool()
    @guard
    def crm_review(proposal_id: str, accept: bool) -> str:
        """Accept or reject a pending CRM proposal with reviewer rights. Stale proposals conflict; acceptance creates a new revision."""
        return call('review', proposal_id=proposal_id, accept=accept)

    @mcp.tool()
    @guard
    def crm_configure(object_type: str, layout: dict | None = None, expected_version: int = 0, reason: str = '') -> str:
        """Configure shared CRM cards as an administrator. Omit layout to read current version. To save pass expected_version and reason. Layout: {title_field: 'Name', sections: [{title: 'Overview', fields: [{field: 'Amount', label: 'Investment', format: 'currency', currency: 'USD'}]}], show_narrative: true, show_related: true}. Formats: text, markdown, number, currency, percent (0..100), date, badge. Discover actual fields with crm_describe. Changes presentation only, never records or access. Reopen the card to see changes."""
        return call('configure', object_type=object_type, layout=layout or {}, expected_version=expected_version, reason=reason)
