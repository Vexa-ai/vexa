# flows/ — the scaffolding conversations

One seed, several ways to open it. Each file is the playbook for setting up a workspace of that kind;
`CLAUDE.md` points at them and the agent reads the one that matches what is being scaffolded.

| File | Read when |
|---|---|
| `personal.md` | a person's own workspace, on their first arrival |
| `shared.md` | a workspace several people work out of — a series, a vendor, a team |
| `global.md` | the organisation tier (`_global`), admin only |

Adding a kind is a new file here plus a row in `CLAUDE.md` — no code change.
