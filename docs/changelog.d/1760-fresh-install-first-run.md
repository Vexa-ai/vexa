- **A fresh `make all` comes up without hand-editing `.env`; the first `make lite` passes its own check (#1759).**
  `make all` / `make up` / `make dev` now mint `INTERNAL_API_SECRET`, `VEXA_FLOWS_API_KEY` and
  `VEXA_FLOWS_TIMELINE_KEY` when `.env` leaves them empty (a value you set is kept), so admin-api and
  flows-api no longer refuse to boot on a fresh install. `make lite` gives each front door up to two
  minutes to answer, so the terminal's first-boot restart no longer fails the first run.
  See [Deployment](/deployment).
