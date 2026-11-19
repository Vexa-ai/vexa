- **One person, many addresses (0.13.4 D35).** A person who signs in with two addresses is one Person:
  the CRM's Person record, found by the address each login verified, with no new identity table. The
  @ picker and the People strip show them once, with "2 accounts", and sharing a chat, adding them to a
  workspace or to a card reaches every one of their logins. They get a card asking whether the accounts
  are all theirs: each login confirms it, they choose the primary address (invites and reminders go
  there), and after that either address opens every workspace either was in. "Not me" splits them. Nothing
  merges without that card. The invite page now says who is signed in and offers "Not you? Use another
  address" before anything joins. agent-api reads `CRM_API_URL` for this; without a CRM nothing changes.
- **Profiles are claimed CRM records (0.13.4 D35).** On first sign-in a person claims the CRM record that
  matches their verified address (a card asks when several match); one record per person, and a second
  login joins it through the merge card. The profile fields (name, photo, headline, company, bio,
  location, time zone, links, contact preferences) are the person's alone to edit; the organisation's
  notes and history stay the organisation's and never appear on a profile. People who share a
  workspace see the profile; a public page at `/p/<handle>` is off until the person turns it on and
  shows only the fields they choose. Unclaiming keeps the record. CRM schema version 7 (one new table).
- **One profile, and grants only after you confirm (0.13.4 D35).** A second login linked by the
  organisation's records gets nothing of yours until you confirm on the card, from each login, that it
  is yours; until then the picker only hints "2 accounts?". The older public profile is retired: it
  can no longer be edited, its entries move into your claimed profile when you claim it (the public
  page stays off until you turn it on), and its old page link now points to `/p/<handle>`.
