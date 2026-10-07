"""Minutes agent MCP composition root with explicit, locked deployment inputs.

The runtime supplies the existing delegated identity and scoped tool handlers.
Connection tools are owned by core/agent; this process never serves meetings MCP.
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
    connections = load('minutes_connection_tools', cfg['connection_tools'])
    connections.register(runtime.mcp, http=runtime._http, subject=runtime.me,
        guard=runtime._anon_guard, scope=runtime.CALL_SCOPE.get, agent_api=runtime.AGENT_API)
    clock = load('minutes_time_tools', cfg['time_tools'])
    clock.register(runtime)
    names = load('minutes_chat_names', cfg['chat_names'])
    names.register(runtime)
    import uvicorn
    uvicorn.run(runtime.app, host=cfg['host'], port=cfg['port'], log_level='warning')


if __name__ == '__main__':
    main(sys.argv[1])
