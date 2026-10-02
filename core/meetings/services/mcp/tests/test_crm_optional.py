import json
from pathlib import Path
import httpx
from vexa_mcp.discover import discover
from vexa_mcp.bind import verify


def test_missing_crm_does_not_change_surface():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
        assembly, specs, bases=discover(client,env={'ADMIN_API_URL':'http://identity'})
    assert 'crm' not in bases
    assert not any(t.name.startswith('crm_') for t in assembly.tools)


def test_unavailable_optional_crm_keeps_existing_mcp_bootable():
    def upstream(r):
        if r.url.host=='crm': raise httpx.ConnectError('offline')
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        assembly,specs,bases=discover(client,env={'ADMIN_API_URL':'http://identity','CRM_API_URL':'http://crm','VEXA_MCP_BOOT_PROBE_ATTEMPTS':'1'})
    tools=verify(assembly,specs)
    assert {t.name for t in tools}=={'crm_describe','crm_search','crm_read','crm_change','crm_history','crm_review'}
    assert all(t.tool.auth=='subject' for t in tools)


def test_bundled_crm_manifest_matches_domain():
    root=Path(__file__).resolve().parents[5]
    snapshot=json.loads((Path(__file__).resolve().parents[1]/'src/vexa_mcp/crm_surface.json').read_text())
    assert snapshot['manifest']==json.loads((root/'core/crm/mcp.tools.v1.json').read_text())
