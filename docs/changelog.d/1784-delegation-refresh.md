- **An agent kept running keeps its Vexa tools (#1784).** A worker's delegation token is replaced
  before it expires for as long as the worker runs, and the worker uses the new one from its next turn;
  the replaced token stops working five minutes later. A long conversation or background job no longer
  loses its meeting and workspace tools after 30 minutes. A worker that has stopped gets no new token.
