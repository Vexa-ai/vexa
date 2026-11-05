- **CRM: an install adds its own fields through `crm-init` (#1986).** `CRM_DEFINITIONS_FILE` names a
  JSON file of extra fields for the organisation's existing types; `crm-init` adds the ones the stored
  definition lacks and changes nothing else. A field the product defines, a type the organisation
  lacks, or a workspace-sharing class is refused. See [CRM](/minutes/crm).
