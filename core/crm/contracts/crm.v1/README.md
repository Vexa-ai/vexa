# crm.v1 — the optional CRM domain's wire (ADR-0047)

`crm.schema.json` defines, under `$defs`:

| Shape | What it is |
|---|---|
| `Record` | What `crm_read` / `POST /read` answers: permitted fields, narrative (full readers only), links, pending proposals, `mentioned_in` (backlinks from the caller's workspaces only), `bound_to`, `match_ids`, `account_id`. Since D4 (0.13.4) fields the caller may not read by their class are **absent**: `match_ids`/`account_id` appear only for a caller who may read `$match_ids` (private by default: owner or administrator), and `bound_to` is limited to the caller's ceiling unless an administrator reads in person. |
| `ReferencesSnapshot` | One workspace's whole set of record references and its roster at one commit — the refs of `workspace.references.changed` and the body of `POST /internal/references`. |
| `ChangeResult` | What `crm_change` answers: `saved`, `proposed` (an agent's change waits for a person), `merged`. |
| `BindResult` | What `crm_bind` answers. |
| `Refusal` | The typed refusals: `shared_conversation`, `human_session_required`, `person_confirmation_required`; the desk's `propose_instead`, `desk_changed` (with the current `version`), `proposals_pending_max`, `proposals_daily_max`, `not_readable`, `ref_kind_not_enabled`, `desk_placements_max`. |
| `Desk` | What `desktop_read` / `POST /desk/read` answers: the person's own desktop — `version` (0 = the default), `layout`, `placements`, pending `proposals`, `saved` arrangement names, `undo_version`. |
| `DeskLayout` / `DeskSection` | The arrangement document with defaults filled in; the sections array order is the section order. |
| `DeskOp` | One declarative desktop operation (`order`, `move`, `group`, `show`/`hide`/`collapse`/`expand`/`section_top`, `section_order`, `section_place`, `section_size`, `place`/`remove`, `filter`, `save`/`restore`). |
| `DeskProposal` | A pending desktop suggestion with its one-line `summary`. |
| `CardView` / `CardFaceBlock` / `CardMovement` | Desktop D2: one card as one viewer reads it — stage and lifecycle, next, waiting, sub-cards they can read, members, the face resolved for them (`locked` where a field is not readable), latest movement, what they may do; `activity` on a single read. |
| `CardProposal` | A pending card proposal the person may answer: a new card (`create`) or a change (`change`). |
| `CardList` / `CardResult` | What `card_list` and a person's `/card/create` · `/card/update` answer. |
| `CardDecision` / `CardInbox` / `CardCounts` / `CardNonce` / `CardDecided` | S5 (PRD 0.13.4 D11–D14): a card of a **kind** (commitment, approval, question, ask, invitation, policy_change) follows a seeded lifecycle template with one structured `next_actor`, a `requester`, `next_check_at`, `chain`, `origin`, the two-phase `handoff` and the `gates` strip; `viewer` is this viewer's role, whether they have seen it since the offer, and the gate actions they may take now. `decisions` are the decision records (append-only revisions with `credential_class=session`, a server nonce and the content hash of the face the signer saw). `/card/inbox` answers the open cards waiting on the person and the counts every surface reads; `/card/nonce` binds a decision to a face; `/card/decide` records it. `Refusal` gains `session_required`. All additive: the new `CardView` properties are optional and `CardList` gained `counts`. Goldens `CardNonce.issued`, `CardView.offered-and-acknowledged` come from `CRM_WRITE_GOLDENS=1 uv run pytest tests/test_card_lifecycle.py -k s5_shapes`. |
| `ArrangeResult` | What `desktop_arrange`, an applied suggestion and Undo answer: `version`, `undo_version`, `summary`. |
| `WorkspaceSettings` / `WorkspaceSettingsMap` / `WorkspaceSettingsResult` | One person's settings for one workspace on the CRM `member_of` relation (pinned, hidden, muted, room_order, level, collapsed, updated_at; `notify` is retired — PRD 0.13.4 S6, the level lives in identity's notification policy); what `workspace_settings_read` (the caller's own rows, by slug) and `workspace_settings_set` (the slug and the full settings after the change) answer. `Refusal` gains `person_only`. |

Goldens in `golden/` are `<Shape>.<case>.json`; `node validate.mjs --check` holds them to the schema
(gate:schema). The goldens are produced by the store itself, not written by hand: the desk's are
written by `CRM_WRITE_GOLDENS=1 uv run pytest tests/test_desk.py` from the values the store returned.

The card shapes (desktop phase D2) were added additively too: `Desk` gained the optional `cards`,
`card_invites` and `card_proposals`, `DeskLayout` the optional `hidden_cards`, and `DeskOp` place/remove
accept `card:` refs. Goldens `CardView.made`, `CardProposal.create`, `Desk.with-cards` come from
`CRM_WRITE_GOLDENS=1 uv run pytest tests/test_cards.py`.

The workspace-settings shapes were added additively too (no existing shape changed; `Refusal` gained
`person_only`). Goldens `WorkspaceSettingsResult.set`, `WorkspaceSettingsMap.one-workspace` and
`Refusal.person-only` come from `CRM_WRITE_GOLDENS=1 uv run pytest tests/test_member_settings.py`.

The desk shapes (desktop phase D1) were added to v1 additively: no existing shape changed, and the
`Refusal` reasons only grew. A back-compatible change re-seals (`contracts.seal.json`).
