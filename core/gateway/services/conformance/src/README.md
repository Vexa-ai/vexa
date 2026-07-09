# src — the conformance harness source

`gateway_conformance/` is the importable package (`pythonpath = ["src","tests"]`):
`contracts` (load sealed schemas by path), `fake_meeting_api` (port-fake downstream),
`gateway_app` (gateway-under-test mirroring `main.forward_request`), `ws_harness`
(the `/ws` unit harness). No internals imported across lanes — contracts consumed read-only.
