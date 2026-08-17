# vexa_artifact_pipeline

| File | Stage | What it owns |
|---|---|---|
| [`ports.py`](ports.py) | — | the six Protocols the spine is written against |
| [`pipeline.py`](pipeline.py) | spine | stage order, idempotency, the run entry |
| [`gateway.py`](gateway.py) | gather | the meeting API as an HTTP consumer, plus a corpus transport for dev |
| [`directory.py`](directory.py) | — | who the artifacts are for, and how to reach them |
| [`gate.py`](gate.py) | gate | composes the `presend-gate` brick; the only authority on recipients |
| [`artifact.py`](artifact.py) | — | **the schema** and the canonical markdown emitter |
| [`labels.py`](labels.py) | — | every fixed string, per language; the header keys are a wire contract |
| [`cues.py`](cues.py) | render | the lexicons the deterministic renderer reads |
| [`render_template.py`](render_template.py) | render | the deterministic renderer — quotes evidence, infers nothing |
| [`render_llm.py`](render_llm.py) | render | the model renderer, declared and raising |
| [`delivery.py`](delivery.py) | deliver | file · command (the postman path) · null sinks |
| [`runlog.py`](runlog.py) | record | the append-only stream, and the idempotency oracle read back from it |
| [`__main__.py`](__main__.py) | trigger | the v0 CLI; the webhook replaces it and nothing else |
