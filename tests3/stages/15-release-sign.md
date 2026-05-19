# Stage: release-deliver-sign

**Level:** release · **Role:** sign · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | human |
| Objective    | Sign that the public release matches the approved staged artifact and is ready for production handoff. |
| Inputs       | release verification report, release notes, public artifact list, production handoff. |
| Outputs      | final release sign in `RELEASE_NOTES.md` or release doc + production handoff artifact. |

## Steps

1. `lib/stage.py assert-is release-sign`.
2. Human reviews release verification and public artifacts.
3. Human signs the final release-doc control point.
4. Save the production handoff artifact for the downstream production system.

## Exit

The public release is signed and ready for downstream production promotion.

## May NOT

- Deploy production from this repo's state machine.
- Sign if public artifacts differ from the staged approval.
- AI must not fill human approval fields.

## Next

`done`.

