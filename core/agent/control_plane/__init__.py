"""control_plane — the agent control plane (HTTP API, dispatch, events, routines).

Turns a ``transcript.v1`` input into a governed action committed to a user
workspace (``workspace.v1``), spawning the worker via ``runtime.v1``.
"""
