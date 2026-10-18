# crm package

The record store uses SQLAlchemy Core for PostgreSQL. SQLite is an offline test
double; successful SQLite tests do not prove PostgreSQL deployment behavior.

`member_settings.py` holds a person's own settings per workspace on the CRM `member_of` relation
(`crm_member_settings`): validation and the store calls, with the person always the caller's subject.
