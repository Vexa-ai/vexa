# `/api/person/<op>`

`me`, `merge` and `profile` forward to crm-api's `/crm/person/<op>` (PRD 0.13.4 D35: one Person, many
addresses) with the signed-in person's own key, behind the same origin check as the desktop routes.
Nothing is stored. The merge card is the person's act only: no deployment key, no agent.
