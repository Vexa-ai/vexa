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
| `{{state}}` | who this is, roughly — `personal:new\|warm group:absent\|new\|warm` |

`{{title}}` and `{{when}}` exist because `{{meeting}}` alone put a **Zoom number** where the
meeting's name belonged: the prepare mail goes out before the meeting row is minted, so its link
carried the native id, and the agent opened by saying it held nothing on `96088138284`. The row is
now planned at prepare time (`flows_steps/meeting.ensure_meeting_row`) and the terminal resolves it
to a title before it substitutes anything.

## Frontmatter

`label:` names the chat in the rail. `mounts:` is the comma-separated workspace set the chat is
over, so context and opening prompt arrive together.

## `{{state}}` — and why a preset needs it

`personal:new` means this is their FIRST chat on this Vexa: a stranger who clicked one button in one
mail about a meeting somebody else organised. `personal:warm` means they have been here before.
`group:absent|new|warm` says whether the meeting is bound to a shared workspace and whether that
workspace has any history behind it.

The preset branches on the string, in prose. Without it the agent has to infer a first contact from
an empty workspace — which is exactly what an existing user with a quiet month also looks like, so
the introduction goes either to everybody or to nobody.

Founder, 2026-09-02, on what a first contact must do: *"it needs to be curious about the user and
needs to build their personal workspace from information available and stating the gaps to the user
so user can help fill the gaps so the meeting preparation becomes like something that starts to make
sense. It's super important to have the first email and chat right."* That is the `personal:new`
branch of `minutes-review-invite` and `minutes-review` — introduce, establish, state the gaps,
invite one fill. `prep` deliberately has NO such branch: the prepare mail never reaches a stranger
(*"if you are only an attendee, meeting prep is probably not what needs to be in the flow for them
at all"*), so a prepare chat is always with somebody who already knows what this is.

