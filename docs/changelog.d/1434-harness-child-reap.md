- **A write-back phase that runs over its budget no longer leaves the harness process running
  (#1434).** When the post-turn write-back phase hit its tool-call or time budget, the agent worker
  stopped reading the Claude Code CLI but, on some CPython builds, never killed it — so the worker
  stayed as busy as it was before the budget fired, one orphaned process per over-budget turn. The
  teardown is now explicit at every hop of the event chain instead of relying on the interpreter's
  garbage collection, so the budget ends the process on every supported interpreter.
