- **A card of a kind made through the generic record create waits on its maker.** Making a
  commitment, approval or question card with `crm_change` create (not the card API) now takes the
  maker as its requester, and refuses a `Requester` naming someone else (403), so a writer cannot
  make a card another person seems to be waiting on and then decide it. See
  [Security and compliance](/security-compliance).
