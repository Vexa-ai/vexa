# terminal/src/app/api/desktop/card — the person's card verbs (desktop D2)

`[op]/` forwards `read|create|update|members|membership|list|seen` to crm-api's `/crm/card/<op>` and `review`
to `/crm/review`, with the signed-in person's own key. Nothing is stored.
