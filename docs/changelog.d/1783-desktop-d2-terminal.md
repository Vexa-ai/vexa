- **Cards on the desktop (#1783, feature `desktop_cards`, off by default; needs `crm` and `desktop`).**
  The desktop's Cards section shows the CRM cards you are on: a face drawn from the card's resolved
  blocks (stage stepper, next, waiting, latest movement, "Stalled", sub-cards done, members, a field
  you cannot read as "Request access"), a card opened with its description, activity, sub-cards and
  members (Move stage to a declared stage, Add member), "Vexa suggests" rows with Add · Not now for
  an agent's proposed cards, invite rows with Accept · Decline, and Take off my desktop with Undo.
  **Make a card** on a chat's, a page's and a file's **…** makes a card about it. The app's server
  relays the card verbs to crm-api with the person's own credential (`POST /api/desktop/card/<op>`)
  and stores nothing. `desktop.v1`'s composed `Desktop` additively carries card tiles,
  `card_proposals`, `card_invites` and `cards_on` (re-sealed).
