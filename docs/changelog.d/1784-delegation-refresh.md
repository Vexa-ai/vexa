- **An agent kept running keeps its Vexa tools (#1784).** A worker's delegation token is replaced at
  half its life for as long as the worker runs, and the worker uses the new one from its next turn; the
  replaced token keeps working until its own expiry, so a turn that started with it has 15 minutes of
  headroom at the default. A long conversation or background job no longer loses its meeting and
  workspace tools after 30 minutes. A turn that runs past its tool access anyway ends with a fault that
  says so ("Vexa tools · tool access expired") and a Retry, instead of carrying on without its tools. A
  worker that has stopped gets no new token.
