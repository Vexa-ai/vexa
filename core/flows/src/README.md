# src

Three packages, one dependency direction: `flows` (the engine) ← `flows_steps` (the tools) ← `flows_defs` (the flows). See each package README.

Set `VEXA_FLOWS_DB_URL` to `postgresql+pg8000://user:password@postgres:5432/flows`.
pg8000 is BSD-3-Clause (Category A), pure Python, and needs no libpq.
