# Retained services

`services/dashboard` preserves the source of the hosted Dashboard deployed in PROD. Dashboard is
deprecated in favor of `clients/terminal` for new self-hosted installations and intentionally uses
its own npm lockfile and service-local validation commands.
