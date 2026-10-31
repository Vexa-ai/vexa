- **Join module: an operator tool creates and deletes real test meetings on Google Meet, Zoom and
  Teams (#1970, #1972).** `core/meetings/modules/join/scripts/platform-meetings.ts` (run with `tsx`)
  creates a meeting with the test accounts — a Meet conference opened to everyone, a Zoom meeting with
  no waiting room, or a Teams meeting whose lobby lets everyone in — and prints a receipt with the
  meeting ids and a URL with no passcode. `delete <receipt.json>` removes the meeting, checks it is
  gone and exits 1 if it is not; `--dry-run` prints the planned requests without credentials.
  Credentials come from the `VERIFY_SECRETS` file outside the repo and are never printed. Harness
  tooling only: no product route, setting or dependency changes. Usage and inputs are in
  `core/meetings/modules/join/scripts/README.md`.
