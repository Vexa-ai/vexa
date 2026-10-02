# records.v1

A record belongs to exactly one tenant. Its typed fields preserve imported API
field names and JSON values. Narrative is Markdown data, never executable code.
Source references retain org, object and record IDs for repeatable imports.

Updates require the current revision. Reviewed changes append evidence and
history; callers cannot silently overwrite a concurrent revision. Authorization
is evaluated before returning fields, narrative, links, search results or history.

The schema defines a record, not a grant. A source access policy that cannot be
translated safely produces a quarantined record, not a public fallback.
