# CRM tests

Offline transaction and authorization tests use SQLite with foreign keys enabled.
PostgreSQL migration, immutable-history roles and actual MCP calls require the
separate deployment harness; offline green is not a deployment receipt.
