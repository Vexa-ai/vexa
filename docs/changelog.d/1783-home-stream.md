- **A new chat opens on the home (#1783).** One ranked stream instead of hint chips: meetings about
  to start, replies waiting on you, a call that just ended with its follow-ups, suggestions with
  their evidence, things to try, routine results and chats to pick up again, with a greeting naming
  the day's state and a line saying what was left out. Every button sends a prompt to the agent;
  dismiss asks why, snooze hides a card for a day, and both re-rank the next home. People on cards
  open a card with Message (the direct chat you share on Vexa, or a draft-only chat to someone
  external) and Open page. For a new person the home is the onboarding: steps from the editable
  `_global/onboarding.md`, done when usage shows them. The home is composed in the background by the
  `home_compose` system flow (not counted against routine quotas), with at most one call to the
  cheapest catalog model, about 6–10 a day for an active person and none per page view. Every
  composition and card outcome is kept (`home_history_retention_days`, default forever), and the
  agent can answer what it suggested (`home_history`) and what Vexa knows about you
  (`customer_profile`, correctable with `customer_correct`). The same `blocks.v1` blocks draw in workspace pages and agent replies (a ```` ```vexa ```` fence), with
  peek, collapse, show more and expand in place. Cards follow the work: a prepared meeting offers
  **Continue** into its prep chat rather than "Prepare" again, the agent records progress with
  `home_item_update`, and open homes update live. Colleagues appear with avatars, company logos and an
  opt-out presence dot. Pictures are fetched and cached server-side, controlled by
  `external_images`. The home proposes automations, connections and how-tos tied to the person's own
  work, mixed by the lifecycle stage defined in the editable `_global/lifecycle.md`.
