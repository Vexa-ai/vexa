- **A room with people from outside the org gets only a record's public fields, everywhere (#1783).**
  A CRM read, history, search, card and card list answered in such a room now carry only an explicit
  list of keys: the record's public fields and its title. Where a record came from, its identifiers,
  the workspaces and pages around it, pending proposals, revision reasons, a card's movement,
  members and boards come back empty, and a search may filter on public fields only. A shared turn
  no longer reads the person's workspace settings, and a personal account's workspace settings are
  kept in its own CRM and go with it.
- **The personal CRM works on Postgres (#1783).** Making an individual's CRM on first use and
  erasing it on account delete now run through two functions the CRM's migration creates, so
  crm-api's own database role can do both. Until it is decided how history is erased, the erase
  keeps the record history, the audit and layout versions, and says in its answer what it kept;
  the records that history names stay as empty, deleted rows.
