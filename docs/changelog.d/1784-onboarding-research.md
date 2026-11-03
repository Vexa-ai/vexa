- **Onboarding research builds a sourced private graph (#1784).** For a new person, the agent reads
  the last 90 days of the connected mail and primary calendars, follows relevant older threads, and
  writes people, companies, projects and meetings into the person's own workspace with their sources.
  Progress is kept per account and survives an interrupted turn; a batch moves on only when every item
  has a source receipt or a stated exclusion. Attachments and secondary calendars are not read in this
  first pass. See [Onboarding research and Extend](/how-to/onboarding-research).
