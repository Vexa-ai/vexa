# terminal/src/app/api/desktop/card/[op] — `POST /api/desktop/card/<op>`

The person's own card verbs (desktop D2, feature `desktop_cards`): `read`, `create`, `update`, `members`,
`membership` and `list` forward to crm-api's `/crm/card/<op>`; `review` (Add / Not now on a card
proposal) forwards to `/crm/review`. The signed-in person's own key, the origin check, nothing stored;
crm's status and body are answered as given. Any other verb is refused here.
