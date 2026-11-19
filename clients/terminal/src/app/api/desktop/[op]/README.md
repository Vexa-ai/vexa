# terminal/src/app/api/desktop/[op] — `POST /api/desktop/<arrange|proposal|undo>`

Forwards to crm-api's `/crm/desk/<op>` with the signed-in person's own key and answers what crm answered
(`desk_changed` 409s included, so the page reloads and retries once). Any other verb is refused here.
