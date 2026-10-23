- **CRM field classes; a room reads only what every member reads (0.13.4 D4).** Each CRM field is
  `private` (owner only), `org` or workspace-shareable; contact history and email-derived data
  default to private, company basics to org. A room or shared chat is never a CRM administrator. It
  reads the fields all its current members may read (or, with `room_fields: classified`, only the
  fields shared with its workspace), never a narrative, identifiers or a private field, and
  `bound_to` stops at its workspaces. Restricted fields are absent from answers. The CRM audit now
  records the principal, the person acted for, the workspace and a search's returned ids. CRM schema
  version 6: one new table, three audit columns added in place.
