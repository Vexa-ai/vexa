- **Cards on the CRM (#1783, desktop D2).** With the new `desktop_cards` feature on (it needs `crm`
  and `desktop`; off by default), a card is a shared CRM record of a card type: a title, what it is
  about, a stage on its type's lifecycle (`_global/POLICIES.md` `lifecycles.card`, default
  `To do > Doing > Done | Dropped`), next, waiting, sub-cards, members and a face composed from
  blocks.v1's new card-face profile, sanitised and resolved per viewer on the server. A workspace's
  board is the cards it references; a desktop shows the cards you are on or placed. New agent tools
  `card_list`, `card_propose` and `card_update_propose` only propose; a person accepts with
  `crm_review`, and an agent never moves a stage. The gateway fronts `/crm/card/list`, `read`,
  `create`, `update`, `members` and `membership` for the terminal. The CRM schema moves to version 4
  (`crm_card_members`; a proposal may name no record yet); run `crm-init` before crm-api. Bootstrap
  adds the `Card` type to existing organisations. "Stalled" follows `card_stalled_days` (default 14),
  and "New" clears for each person when they open the card (`crm_card_seen`).
