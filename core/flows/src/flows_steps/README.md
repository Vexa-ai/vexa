# flows_steps — step implementations

The TOOLS a flow names: one function per capability, registered by `__name__`. Real adapters call
domain HTTP APIs (meeting-api, agent-api, the notifier) and are OUTSIDE the engine's import graph;
`fakes.py` mirrors the same names against `FakeWorld` so every fixture and the storm run with zero
domains attached. A step answers `Done(result)`, `Wait(seconds|until)`, or `Block(reason, deadline)`
— nothing else; effects go through the receipt the engine reserved for it.
