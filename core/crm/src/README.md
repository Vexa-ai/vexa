# CRM implementation

`crm/schema.py` defines the CRM-owned tables. `crm/store.py` owns transactions
and tenant-scoped authorization. Adapters must use that boundary rather than
reading tables directly. Identity users and native meetings remain external IDs.
