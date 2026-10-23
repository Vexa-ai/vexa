"""Inspect an explicit Minutes deployment; fail closed on drift or missing tools.

Run on the deployment host. The lock contains no environment values or secrets.
This command never starts, stops, deploys, or repairs a service.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def command(*args):
    return subprocess.check_output(args, text=True, timeout=45, stderr=subprocess.PIPE)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(path):
    root = Path(path)
    rows = []
    for file in sorted(root.rglob('*')):
        relative = file.relative_to(root)
        if any(part in {'.venv', '.git', '__pycache__', 'node_modules'} for part in relative.parts):
            continue
        if file.is_file() and file.suffix in {'.py', '.json', '.toml'}:
            rows.append((str(relative), digest(file)))
    if not rows:
        raise ValueError('Runtime source tree is empty')
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def compare(expected, actual):
    findings = []
    for section in ('files', 'containers', 'units', 'routes', 'trees'):
        for key, value in expected.get(section, {}).items():
            if actual.get(section, {}).get(key) != value:
                findings.append(f'{section}: {key} differs from the deployment lock')
    missing = set(expected['required_worker_tools']) - set(actual['worker_tools'])
    if missing:
        findings.append('Worker MCP missing tools: ' + ', '.join(sorted(missing)))
    if actual['routes'].get('worker_mcp') != expected['required_worker_route']:
        findings.append('Worker MCP bypasses the required gateway route')
    if actual['routes'].get('agent_worker_image') != actual['routes'].get('runtime_worker_image'):
        findings.append('Agent-api and runtime declare different worker images')
    if not actual['worker_auth_rejects_invalid']:
        findings.append('Worker MCP did not reject an invalid credential')
    if any(name.startswith(prefix) for name in actual['worker_tools'] for prefix in expected.get('forbidden_tool_prefixes', [])):
        findings.append('Disabled tool domain is exposed')
    findings.extend(actual['health_findings'])
    return findings


PROBE = r'''
import os,json,httpx,hashlib,time,uuid,redis
from shared.delegation import mint_delegation
from control_plane.delegation_revocation import mark_live
url=os.environ['VEXA_MCP_URL']
# Discovery only. No tool invocation, user record, transcript or credential is returned.
jti=uuid.uuid4().hex
token=mint_delegation(os.environ['VEXA_MCP_DELEGATION_SECRET'],subject='stack-discovery',regime='autonomous',workspaces=[],ttl_sec=60,jti=jti)
# Identity admits a delegation token only while agent-api holds it live; hold this one for its 60 s.
mark_live(redis.from_url(os.environ.get('REDIS_URL') or 'redis://redis:6379/0'),jti=jti,exp=int(time.time())+60)
headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'}
with httpx.Client(timeout=15) as client:
 def call(method,params,id):
  r=client.post(url,headers=headers,json={'jsonrpc':'2.0','id':id,'method':method,'params':params})
  r.raise_for_status()
  if r.headers.get('Mcp-Session-Id'): headers['Mcp-Session-Id']=r.headers['Mcp-Session-Id']
  if 'text/event-stream' in r.headers.get('content-type',''):
   return json.loads(next(l[6:] for l in r.text.splitlines() if l.startswith('data: ')))
  return r.json()
 call('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'stack-discovery','version':'1'}},1)
 result=call('tools/list',{},2)
 bad=client.post(url,headers={**headers,'Authorization':'Bearer vxd_invalid'},json={'jsonrpc':'2.0','id':3,'method':'tools/list','params':{}})
 print(json.dumps({'tools':sorted(t['name'] for t in result['result']['tools']), 'invalid_rejected':bad.status_code in (401,403)}))
'''


def observe(lock):
    actual = {key: {} for key in ('files', 'containers', 'units', 'routes', 'trees')}
    actual.update(worker_tools=[], worker_auth_rejects_invalid=False, health_findings=[])
    for path in lock['files']:
        actual['files'][path] = digest(path) if Path(path).is_file() else None
    for path in lock.get('trees', {}):
        actual['trees'][path] = tree_digest(path)
    envs = {}
    for name in lock['containers']:
        item = json.loads(command('docker', 'inspect', name))[0]
        actual['containers'][name] = item['Image']
        state = item['State']
        if state['Status'] != 'running' or state.get('Health', {}).get('Status', 'healthy') != 'healthy':
            actual['health_findings'].append(f'Container not ready: {name}')
        envs[name] = dict(v.split('=', 1) for v in item['Config'].get('Env', []) if '=' in v)
    for name in lock.get('retired_containers', []):
        item = json.loads(command('docker', 'inspect', name))[0]
        if item['State']['Running'] or item['HostConfig']['RestartPolicy']['Name'] != 'no':
            actual['health_findings'].append(f'Retired container enabled: {name}')
    for unit in lock['units']:
        props = dict(line.split('=', 1) for line in command('systemctl', '--user', 'show', unit,
            '-p', 'ActiveState', '-p', 'MainPID').splitlines() if '=' in line)
        if props['ActiveState'] != 'active':
            actual['health_findings'].append(f'Unit not active: {unit}')
        cmdline = Path('/proc', props['MainPID'], 'cmdline').read_bytes().decode().strip('\0').split('\0')
        actual['units'][unit] = cmdline
    for unit in lock.get('retired_units', []):
        state = command('systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value').strip()
        if state != 'inactive':
            actual['health_findings'].append(f'Retired unit is not inactive: {unit}')
    for name, (container, key) in lock['route_sources'].items():
        actual['routes'][name] = envs[container].get(key, '')
    probe = json.loads(command('docker', 'exec', lock['probe_container'], 'python', '-c', PROBE))
    actual['worker_tools'] = probe['tools']
    actual['worker_auth_rejects_invalid'] = probe['invalid_rejected']
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock', type=Path)
    parser.add_argument('--inventory', action='store_true', help='Print sanitized observations, including drift')
    args = parser.parse_args()
    try:
        lock = json.loads(args.lock.read_text())
        actual = observe(lock)
        findings = compare(lock, actual)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Commands may carry credentials in stderr; never echo exception output.
        print(json.dumps({'status': 'source_failure', 'detail': 'Deployment observation incomplete'}))
        return 2
    output = {'status': 'blocked' if findings else 'verified', 'findings': findings,
              'worker_tool_count': len(actual['worker_tools']),
              'worker_tool_hash': hashlib.sha256(json.dumps(actual['worker_tools']).encode()).hexdigest()}
    if args.inventory:
        output['observed'] = actual
    print(json.dumps(output, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
