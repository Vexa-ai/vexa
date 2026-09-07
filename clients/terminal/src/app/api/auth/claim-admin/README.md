# auth/claim-admin

`POST` — claim the administrator role from an **already existing session**. The way out of a dead
end, not a second sign-in door.

The role was only ever claimable inside `findOrCreateUserToken`, i.e. only while walking through a
sign-in door. An instance that acquired live sessions before the company-layer gate shipped
therefore had no reachable claim at all: a valid months-old cookie, `admin_exists` false, and a
cookie never traverses a sign-in door twice. The screen said "this instance is not set up" and
nothing in the product could set it up. This route is the missing edge.

1. Identity comes **only** from `validateAuthToken` on the `vexa-token` cookie. The
   `vexa-user-info` cookie is display-only — `httpOnly` stops a script reading it, not a
   hand-written `Cookie` header — and a claim that trusted it would hand the highest privilege
   this product has to anyone who can type `curl`.
2. Refuses with `409` when an admin already exists, and tells the client to reload: the claim
   screen it is showing is stale.
3. Delegates the write to admin-api `/internal/bootstrap-admin`, which serialises concurrent
   claims under an advisory lock and is a no-op once an admin exists — so racing tabs are safe and
   the `admin_exists` check above is a courtesy for the message, never the safety property.

**Fails CLOSED, and this is the opposite direction from the rest of the gate.** Elsewhere an
unreachable probe must not lock everybody out of a working instance, so those paths fail open.
Here, guessing wrong *grants admin*: the cost of refusing is one more button press, the cost of
allowing is a stranger becoming the administrator during an outage.
