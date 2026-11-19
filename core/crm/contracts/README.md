# CRM contracts

`crm.v1` is the optional CRM domain's wire: the record envelope shared by HTTP, MCP and the
terminal's record page, the references snapshot a workspace commit carries, and the typed refusals.
Object definitions (Person, Company, Deal, Project, imported types) are configuration over this
envelope. Changes to the envelope require a versioned compatibility decision.
