"""Minutes agent MCP composition root with explicit, locked deployment inputs.

The rig supplies the delegated identity and its own scoped tools. The agent domain's tools
(Connections, the person's clock, chat naming) are registered from agent_tools.py, which calls
the same agent-api routes the product's assembled MCP binds, through the gateway with the caller's
own credential; this file is the only place the rig's internals are wired into those explicit
ports. This process never serves meetings MCP.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent_call(runtime):
    """The `call` port: agent-api, as the person the current MCP call acts for, THROUGH THE GATEWAY.

    The gateway resolves the caller's own credential and signs that identity (gateway-identity.v1);
    agent-api verifies it and forwards it to the credential broker, which acts for a person only
    on that signature — the internal tier does not reach Connections. A worker's delegation token
    (`vxd_`) goes to the gateway as itself, so identity resolves its regime and workspace ceiling
    and the gateway signs them; any other caller goes as the person's own gateway key (`_gw_http`,
    which re-mints once on a revoked key). `/api/x` on agent-api is `/agent/x` at the gateway."""
    def call(method, path, body=None, timeout=60):
        if not path.startswith('/api/'):
            raise ValueError('agent-api routes are /api/...')
        route = '/agent/' + path[len('/api/'):]
        token = (runtime.CALL_TOKEN.get() or '').strip()
        if token.startswith(runtime.DELEGATION_PREFIX):
            return runtime._http(method, runtime.GATEWAY + route, {'X-API-Key': token}, body,
                                 timeout=timeout)
        return runtime._gw_http(runtime.me(), method, route, body, timeout=timeout)
    return call


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
    imports.register_workspace_import_tools(runtime.mcp, http=runtime._http,
        subject=runtime.me, guard=runtime._anon_guard, agent_api=runtime.AGENT_API)
    # Product mail tools must never query the development outbound mail sink.
    runtime.mcp.remove_tool('mail_inbox')
    runtime.mcp.remove_tool('mail_read')
    tools = load('minutes_agent_tools', cfg['agent_tools'])
    call = agent_call(runtime)
    tools.register(runtime.mcp, call=call, guard=runtime._anon_guard)
    original = runtime.whats_waiting
    runtime.mcp.remove_tool('whats_waiting')
    runtime.mcp.tool()(runtime._anon_guard(tools.with_time_context(original, call)))
    import uvicorn
    uvicorn.run(runtime.app, host=cfg['host'], port=cfg['port'], log_level='warning')


if __name__ == '__main__':
    main(sys.argv[1])
