- **Terminal errors now speak user truth (#533).** Every error a terminal surface shows states
  what happened in the user's vocabulary ("Couldn't reach the Vexa server — check that the stack
  is running.", "Your API key was rejected — sign in again.", or the backend's own reason
  verbatim) instead of transport plumbing like `/api/... → 502: upstream unreachable:
  ConnectError`. The full technical string is preserved on the browser console for operators. A
  new presenter seam (`presentError` beside `ApiError`) plus a grep-guard test keep the 46 former
  raw render sites — and any future one — honest.
