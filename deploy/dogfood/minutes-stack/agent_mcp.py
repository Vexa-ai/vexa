"""Minutes agent MCP composition root with explicit, locked deployment inputs.

The rig supplies the delegated identity and its own scoped tools. The agent domain's tools
(Connections, the person's clock, chat naming) are registered from agent_tools.py and reach
agent-api through the gateway with the caller's own credential: a person's own gateway key on the
REST route, a worker's delegation token as a call to the same-named tool on the gateway's `/mcp`.
This file is the only place the rig's internals are wired into those explicit ports. This process
never serves meetings MCP.
"""
import contextvars
import importlib.util
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: The MCP protocol revision this client speaks to the gateway.
MCP_PROTOCOL = '2025-06-18'

#: A hop marker this process puts on its own calls to the gateway's `/mcp`. Should the gateway's
#: MCP upstream ever be this process, the call arrives back here carrying it, and is refused rather
#: than calling itself until something times out.
HOP_HEADER = 'x-vexa-rig-hop'
HOP = secrets.token_urlsafe(12)
INBOUND_HOP = contextvars.ContextVar('minutes_rig_inbound_hop', default='')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _transport(method, url, headers, payload, timeout):
    """One HTTP exchange: (status, lower-cased response headers, body text). Never raises."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read().decode()
    except Exception as e:  # noqa: BLE001 — an unreachable gateway is an answer, not a crash
        return 0, {}, json.dumps({'detail': f'gateway unreachable ({type(e).__name__})'})


def _rpc_message(text, content_type):
    """The JSON-RPC message in a response: the JSON body, or the last `data:` event of a stream."""
    if 'text/event-stream' in (content_type or ''):
        events = [line[5:].strip() for line in (text or '').splitlines() if line.startswith('data:')]
        text = events[-1] if events else ''
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _http_error(text):
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        body = {'detail': (text or '').strip()[:500]}
    return body if isinstance(body, dict) else {'detail': body}


def _tool_answer(result):
    """A `tools/call` result as the (status, body) an agent-api route would have answered.

    A refusal is the gateway MCP's rendered block (`vexa_mcp.tool_errors`): words first, the
    unwrapped upstream body on its last line, `HTTP <status>` leading when nothing better was
    authored. Standing notices, when present, follow the block after a blank line."""
    content = (result or {}).get('content') or []
    text = next((c.get('text', '') for c in content if c.get('type') == 'text'), '')
    block = text.split('\n\n')[0]
    if not result.get('isError'):
        for candidate in (text, block):
            try:
                return 200, json.loads(candidate)
            except (TypeError, ValueError):
                continue
        return 200, text
    lines = block.split('\n')
    try:
        detail = json.loads(lines[-1])
    except (TypeError, ValueError):
        detail = lines[-1].strip()
    status = re.match(r'HTTP (\d{3})\b', lines[0])
    if status:
        code = int(status.group(1))
    elif isinstance(detail, dict) and detail.get('status') == 'refused':
        code = 403
    else:
        code = 502
    return code, {'detail': detail}


def gateway_tool(gateway, token, tool, arguments, timeout=60, transport=_transport):
    """Call the gateway's MCP tool `tool` with a worker's delegation token: (status, body).

    The gateway admits a `vxd_` token on `/mcp` and nowhere else, signs the identity it resolves
    (with the delegation's regime and workspace ceiling) onto the hop, and its assembled tool calls
    the same agent-api route the person's REST path does. One session per call, closed after."""
    url = gateway.rstrip('/') + '/mcp'
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'Authorization': 'Bearer ' + token, HOP_HEADER: HOP}
    status, rh, text = transport('POST', url, headers, {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': MCP_PROTOCOL, 'capabilities': {},
                   'clientInfo': {'name': 'minutes-rig', 'version': '1'}}}, timeout)
    if status != 200:
        return status, _http_error(text)
    init = _rpc_message(text, rh.get('content-type')) or {}
    if 'error' in init:
        return 502, {'detail': str((init['error'] or {}).get('message') or 'MCP initialize failed')}
    session = rh.get('mcp-session-id', '')
    if session:
        headers['Mcp-Session-Id'] = session
    headers['MCP-Protocol-Version'] = str((init.get('result') or {}).get('protocolVersion')
                                          or MCP_PROTOCOL)
    try:
        status, _, text = transport('POST', url, headers,
                                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                    timeout)
        if status not in (200, 202):
            return status, _http_error(text)
        status, rh, text = transport('POST', url, headers, {
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': tool, 'arguments': arguments}}, timeout)
        if status != 200:
            return status, _http_error(text)
        answer = _rpc_message(text, rh.get('content-type')) or {}
        if 'error' in answer:
            return 502, {'detail': str((answer['error'] or {}).get('message') or 'MCP call failed')}
        return _tool_answer(answer.get('result') or {})
    finally:
        if session:
            transport('DELETE', url, headers, None, timeout)


def agent_forward(manifest_path):
    """The agent domain's declared edge mapping, `(edge_prefix, upstream_prefix)`, read from its
    tool manifest (`forward` in `core/agent/mcp.tools.v1.json`, held equal to `routes.v1.json`'s).
    A manifest without a readable one stops the boot: this adapter spells no paths of its own."""
    doc = json.loads(Path(manifest_path).read_text())
    raw = doc.get('forward') if isinstance(doc, dict) else None
    edge = (raw or {}).get('edge_prefix') if isinstance(raw, dict) else None
    upstream = (raw or {}).get('upstream_prefix') if isinstance(raw, dict) else None
    ok = all(isinstance(p, str) and len(p) > 2 and p.startswith('/') and p.endswith('/')
             for p in (edge, upstream))
    if not ok:
        raise ValueError(f'{manifest_path}: forward must be {{"edge_prefix": "/x/", '
                         f'"upstream_prefix": "/y/"}} (got {raw!r})')
    return edge, upstream


def agent_call(runtime, forward, transport=_transport):
    """The `call` port: agent-api, as the person the current MCP call acts for, THROUGH THE GATEWAY.

    The gateway resolves the caller's own credential and signs that identity (gateway-identity.v1);
    agent-api verifies it and forwards it to the credential broker, which acts for a person only
    on that signature — the internal tier does not reach Connections. A worker's delegation token
    (`vxd_`) is an MCP credential: the gateway admits it on `/mcp` only, so it goes there as itself,
    calling the gateway's tool of the same name (`tool`), and identity resolves its regime and
    workspace ceiling. Any other caller goes as the person's own gateway key (`_gw_http`, which
    re-mints once on a revoked key) to the REST route, mapped by the domain's declared `forward`
    (`agent_forward`): an upstream path under `upstream_prefix` is the same tail under
    `edge_prefix` at the gateway."""
    edge, upstream = forward

    def call(method, path, body=None, timeout=60, tool=None):
        if not path.startswith(upstream):
            raise ValueError(f'agent-api routes are {upstream}...')
        token = (runtime.CALL_TOKEN.get() or '').strip()
        if token.startswith(runtime.DELEGATION_PREFIX):
            if not tool:
                raise ValueError('a delegated call names the gateway MCP tool for its route')
            if INBOUND_HOP.get() == HOP:
                return 508, {'detail': "the gateway's /mcp leads back to this rig: point the "
                                       "gateway's MCP_URL at the product MCP service"}
            return gateway_tool(runtime.GATEWAY, token, tool, body or {}, timeout, transport)
        return runtime._gw_http(runtime.me(), method, edge + path[len(upstream):], body,
                                timeout=timeout)
    return call


def hop_guard(app):
    """Record, per request, the hop marker a call arrived with (see `HOP`)."""
    async def guarded(scope, receive, send):
        if scope.get('type') == 'http':
            INBOUND_HOP.set(next((v.decode('latin-1') for k, v in scope.get('headers') or []
                                  if k.decode('latin-1').lower() == HOP_HEADER), ''))
        return await app(scope, receive, send)
    return guarded


def main(config_path):
    cfg = json.loads(Path(config_path).read_text())
    environment = json.loads(Path(cfg['environment_file']).read_text())
    os.environ.update({k: str(v) for k, v in environment.items()
                      if k.startswith(('VEXA_', 'CRM_')) or k == 'INTERNAL_API_SECRET'})
    os.environ['PORT'] = str(cfg['port'])
    os.environ['VEXA_PUBLIC_MCP_URL'] = f"http://{cfg['host']}:{cfg['port']}/mcp"
    os.environ['VEXA_AGENT_SRC'] = cfg['agent_source']
    sys.path.insert(0, str(Path(cfg['runtime']).parent))
    runtime = load('minutes_agent_runtime', cfg['runtime'])
    if cfg.get('crm_enabled', False):
        crm = load('minutes_crm_tools', cfg['crm_tools'])
        crm.register_crm_tools(runtime.mcp, base_url=os.environ['CRM_API_URL'], subject=runtime.me,
            scope=runtime.CALL_SCOPE.get, user_key=runtime._user_key, http=runtime._http,
            guard=runtime._anon_guard)
    imports = load('minutes_import_tools', cfg['import_tools'])
    imports.register_workspace_import_tools(runtime.mcp, git=runtime._agent_git,
        subject=runtime.me, guard=runtime._anon_guard)
    # Product mail tools must never query the development outbound mail sink.
    runtime.mcp.remove_tool('mail_inbox')
    runtime.mcp.remove_tool('mail_read')
    tools = load('minutes_agent_tools', cfg['agent_tools'])
    call = agent_call(runtime, agent_forward(Path(cfg['agent_source']) / 'mcp.tools.v1.json'))
    tools.register(runtime.mcp, call=call, guard=runtime._anon_guard)
    original = runtime.whats_waiting
    runtime.mcp.remove_tool('whats_waiting')
    runtime.mcp.tool()(runtime._anon_guard(tools.with_time_context(original, call)))
    import uvicorn
    uvicorn.run(hop_guard(runtime.app), host=cfg['host'], port=cfg['port'], log_level='warning')


if __name__ == '__main__':
    main(sys.argv[1])
