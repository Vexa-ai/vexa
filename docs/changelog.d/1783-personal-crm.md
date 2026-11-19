- **A personal CRM for individual accounts, off by default (#1783).** When `VEXA_FEATURES` names
  `personal_crm` (`all` does not switch it on), an account that identity has made an individual one
  (its own org of one) is served its own CRM. It never sees the company's records, and the company
  never sees its records. Off, nothing changes.
