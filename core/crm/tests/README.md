# CRM tests

Offline transaction and authorization tests use SQLite with foreign keys enabled.
PostgreSQL migration, immutable-history roles and actual MCP calls require the
separate deployment harness; offline green is not a deployment receipt.

`test_member_settings.py` and `test_member_settings_api.py` cover workspace settings: defaults, partial
merges, validation, two subjects, the `person_only` refusal and the `desktop` switch.
