# Lite Human-Eyeball Note

Status: pass - evidence disposition recorded; Lite execution remains blocked by isolation.

Lite was not started for this pack because the official Lite workflow is not isolated on this host.

The documented `deploy/lite/Makefile` path uses fixed names and host ports:

- `vexa-lite`
- `vexa-postgres`
- 3000, 8056, 8057

Those names and ports are already occupied by unrelated local Lite lanes. Starting or stopping Lite for this pack would disrupt another validation environment.
