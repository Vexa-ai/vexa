# crm package

The record store uses SQLAlchemy Core for PostgreSQL. SQLite is an offline test
double; successful SQLite tests do not prove PostgreSQL deployment behavior.

`member_settings.py` holds a person's own settings per workspace on the CRM `member_of` relation
(`crm_member_settings`): validation and the store calls, with the person always the caller's subject.

`field_classes.py` classifies each field `private`, `org` or workspace-shareable (`crm_field_classes`,
seeded by `bootstrap`) and defines `FieldView`, what one caller reads of one record. `Store._view`
builds it, including a shared caller's room ceiling, and every read path takes its fields from it.

`persons.py` is one Person, many addresses (D35): an account resolves to its Person by verified
address, `account:` match identifiers link further accounts, and the merge card (a change proposal
with its own `merge_*` statuses) is confirmed by each linked account itself. No table of its own.

`person_profiles.py` is the profile: a claimed Person record (`crm_profiles`, one per account and
per record), its person-owned fields, the public page and unclaim. It never reads the org's fields.

`card_templates.py` (S5, PRD 0.13.4 D11–D12) holds the seeded lifecycle templates of the card kinds —
commitment, approval (also invitation, policy_change), question (also ask) — and who may pass each gate.
`card_ops.Cards` makes a card of a kind in its first stage and offers it to its one `NextActor`;
`decide` passes a gate as a decision record (a revision with `decision`, `credential_class=session`,
`nonce`, `content_hash`; nonces in `crm_card_nonces`), `seen` is the terminal's render-in-view, and
`inbox`/`counts` are the one predicate every surface counts with. `store.Store.begin` writes the pending
audit row inside the operation's transaction (H9), and `store._envelope` computes a fact's audience (D8).
