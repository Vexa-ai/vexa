# artifact-pipeline/src

One package, `vexa_artifact_pipeline`. Its front door is
[`vexa_artifact_pipeline/__init__.py`](vexa_artifact_pipeline/); the seams are
[`ports.py`](vexa_artifact_pipeline/ports.py) and the order the stages run in is
[`pipeline.py`](vexa_artifact_pipeline/pipeline.py).

The package imports one sibling brick — `presend_gate`
(`core/meetings/modules/presend-gate`) — and nothing else from the tree. It reaches the
meeting API over HTTP as an ordinary consumer and the chat-door postman over its command
line, so neither is a Python dependency.
