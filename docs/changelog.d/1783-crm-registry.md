- **A CRM beside workspaces (#1783, ADR-0047).** An optional module holds the organisation's stable
  entities — people, companies, deals and projects — with one id each, match identifiers (emails,
  domains, calendar event ids) and merges. A signed-in account is attached to its person record.
  Workspace pages link records instead of copying them, and each record lists the pages that mention
  it, only from workspaces the reader belongs to. Access is the CRM's policy plus workspace bindings:
  an administrator binds a workspace to a record and its members get read (contributors: write) for
  as long as they are members. Agents propose changes and a person accepts them on the record page;
  in a chat other people read, the agent uses only records every participant can read. The CRM is
  reached through the chat (`crm_*` tools in the one MCP) and one record page. Turn it on with the
  compose profile `crm`. See [CRM](/minutes/crm).
