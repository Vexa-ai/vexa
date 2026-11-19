"""flows_delivery — THE ONE DELIVERY GATE every nudge passes through (PRD 0.13.4 D15, D32, M4; S6).

flows owns time, SMTP and receipts (D15), so the decision "does this person hear about this item
outside the Inbox, and when" is taken here and nowhere else:

    item ─ arm(next_check_at) ─▶ in-app notice ─ idle window ─▶ ONE email ─▶ hourly/daily digest
                                   │                │                │
                                   └── stops the moment crm says the item is seen or acted on ─┘

* ``policy``    the person's notification policy (identity person settings, key ``notifications``)
                clamped to the deployment's global floors and ceilings; quiet hours in local time.
* ``templates`` the fixed bodies (M4): title + who + one "Open to decide" link. A floor (D38
                ``nudge_body = template_only``): there is no parameter that can carry free text.
* ``store``     the ``delivery`` table (one row per person and item — the keyed schedule) and the
                ``delivery_send`` ledger (one row per delivery outside the Inbox — the D32 unit).
* ``gate``      the pure decision for one row at one instant.
* ``ports``     the doors: crm (seen / acted / the recipient's template fields), agent-api (the
                in-app notice and its read state), identity (policy, timezone, address), the mail
                channel (``flows_steps.notify``; capture mode reaches a sink, never a mailbox).
* ``run``       the worker pass: wake due rows, decide, act, record.

Nothing here stores content: a card's title is fetched from crm at send time, for that recipient,
and rendered into the template; the row keeps refs and timestamps only (D8, M5).
"""
