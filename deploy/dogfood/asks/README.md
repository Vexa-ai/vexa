# asks/ — what an emailed link SAYS when it opens a chat

Every mail this deployment sends carries at most one link, and that link names a **preset**:
`?ask=<name>` resolves to `_global/asks/<name>.md`, which the terminal reads at click time and
sends as the chat's first turn. The URL never carries prompt text — a link that could would let
anyone who can send mail drive the recipient's agent.

These files are the SOURCE of the live presets. The live copies are the ones a click actually
reads, at `/workspaces/_global/asks/<name>.md` on the stack; edit both, with the same content, or
the source lies. Nothing is rebuilt when they change — the next click picks up the new text.

## Substitutions the terminal makes

| token | becomes |
|---|---|
| `{{title}}` | the meeting's **title**, resolved from the row behind `?meeting=` |
| `{{when}}` | when it is (or was), in the reader's own locale |
| `{{meeting}}` | the meeting's row id — the ref, for the agent to open it |
| `{{ws}}` | the first mounted workspace |
| `{{today}}` | today's date, ISO |

`{{title}}` and `{{when}}` exist because `{{meeting}}` alone put a **Zoom number** where the
meeting's name belonged: the prepare mail goes out before the meeting row is minted, so its link
carried the native id, and the agent opened by saying it held nothing on `96088138284`. The row is
now planned at prepare time (`flows_steps/meeting.ensure_meeting_row`) and the terminal resolves it
to a title before it substitutes anything.

## Frontmatter

`label:` names the chat in the rail. `mounts:` is the comma-separated workspace set the chat is
over, so context and opening prompt arrive together.
