"""flows_authoring — write flows as PYTHON FILES, ship them as ROWS.

The file is imported on the AUTHOR'S side only (CLI/CI); `flow(...)` is extraction, not
execution — it yields the exact row flows-api stores and the worker hot-loads. The worker never
imports submitted files: a flow file that tries to smuggle callables is rejected here.

    python3 flows_authoring.py submit my_flow.py [--api http://localhost:18200] [--key …]
    python3 flows_authoring.py check  my_flow.py            # extract + validate only
    python3 flows_authoring.py export [name]                # live registry → canonical Python
                                                            # (row → file is lossless: the flow
                                                            # language is data in BOTH directions)
"""
from __future__ import annotations

import json
import sys
import urllib.request

_DEFS: list[dict] = []


def flow(*, name: str, on: str, steps: list[str], params: dict | None = None,
         activate: bool = True) -> dict:
    """Declare a flow. Steps are NAMES from the deployed vocabulary — never callables: this file
    compiles to data, and data cannot execute in the worker."""
    if not all(isinstance(s, str) for s in steps):
        raise TypeError("steps must be step NAMES (strings) — flow files carry no code; "
                        "new behavior is a reviewed step in the image, or agent params")
    if params is not None:
        json.dumps(params)                       # params must be pure data too
    d = {"name": name, "on_event": on, "steps": list(steps),
         "params": params or {}, "activate": activate}
    _DEFS.append(d)
    return d


def _extract(path: str) -> list[dict]:
    # When run as a CLI this file is module "__main__", but the flow file imports
    # "flows_authoring" — a second instance with its own _DEFS. Collect from THAT one.
    import importlib
    import importlib.util
    fa = importlib.import_module("flows_authoring") if __name__ == "__main__" else sys.modules[__name__]
    fa._DEFS.clear()
    spec = importlib.util.spec_from_file_location("flow_file", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                 # author-side import — the only place this runs
    if not fa._DEFS:
        raise SystemExit(f"{path}: no flow(...) declarations found")
    return list(fa._DEFS)


def _export(api: str, key: str, only: str | None) -> int:
    req = urllib.request.Request(f"{api}/flows")
    req.add_header("X-Flows-Admin-Key", key)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    newest: dict[str, dict] = {}
    for f in data["flows"]:
        if only and f["name"] != only:
            continue
        cur = newest.get(f["name"])
        if cur is None or f["version"] > cur["version"]:
            newest[f["name"]] = f
    print("# canonical export — regenerate any time; row → Python is lossless")
    print("from flows_authoring import flow\n")
    for f in sorted(newest.values(), key=lambda x: x["name"]):
        print(f"# v{f['version']} · source: {f['source']} · status: {f.get('status','active')}")
        print(f"flow(name={f['name']!r}, on={f['on']!r},")
        print(f"     steps={f['steps']!r}," )
        print(f"     params={f.get('params') or {}!r})\n")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "export":
        api = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--api"),
                   "http://localhost:18200")
        key = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--key"), "changeme")
        only = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
        return _export(api, key, only)
    if len(sys.argv) < 3 or sys.argv[1] not in ("submit", "check"):
        print(__doc__)
        return 2
    cmd, path = sys.argv[1], sys.argv[2]
    api = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--api"),
               "http://localhost:18200")
    key = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--key"), "changeme")
    for d in _extract(path):
        print(f"  {d['name']} on={d['on_event']} steps={d['steps']}")
        if cmd == "check":
            continue
        req = urllib.request.Request(f"{api}/flows", method="POST",
                                     data=json.dumps(d).encode())
        req.add_header("content-type", "application/json")
        req.add_header("X-Flows-Admin-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                print("   →", r.read().decode().replace("\n", " ")[:120])
        except urllib.error.HTTPError as e:
            print("   ✗", e.read().decode()[:200])
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
