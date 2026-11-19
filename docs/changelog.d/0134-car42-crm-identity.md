- **Only the identity service binds a sign-in to a CRM person (A2-b, A2-c).** A CRM writer can no
  longer merge someone's org record into their own person record, or move their own address onto
  someone else's record, and have that person's sign-in (or their own) land there: an address a
  merge carried, and a second address listed on a record someone already signs in with, bind nobody.
  A record created with the person's `email:` identifier still becomes theirs at their first
  verified sign-in, and a person still joins their own logins on their merge card. Upgrading runs
  CRM schema 9 (`crm-init` / the `crm-schema` Job), which records who set each address. See
  [CRM](/minutes/crm).
- **A card's next actor is not a field any writer sets.** The person waiting is fixed when a card is
  made; only they or the current next actor hand it on, and never to themselves. See
  [Security and compliance](/security-compliance).
- **A draft you wrote is replaced only on your press**, on the card naming the recipient; the agent's
  word is not enough. **Invite emails** say the workspace and room names only as one short line with
  no web address, email address or domain in it. See [Workspaces](/minutes/workspaces).
