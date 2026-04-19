# security-hygiene

Repo-wide, ops-level security hygiene. Not tied to a single service or
user-visible feature — lives in `features/` so the standard `dods.yaml`
+ AUTO-DOD machinery treats it the same way as any other feature.

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 95%**

## Scope

| concern                            | DoD id                           | check binding                   |
|------------------------------------|----------------------------------|---------------------------------|
| CVE-2025-43859 (h11 transitive)    | `h11-pinned-safe`                | `H11_PINNED_SAFE_EVERYWHERE`    |
| OpenAPI / Swagger info disclosure  | `docs-env-gated-everywhere`      | `DOCS_ENV_GATED_EVERYWHERE`     |
| Transitive npm vulns in vexa-bot   | `vexa-bot-no-high-npm-vulns`     | `VEXA_BOT_NO_HIGH_NPM_VULNS`    |

CVE-specific feature-level fixes live with the feature that owns them
(auth-and-limits for CVE-2026-25058; webhooks for CVE-2026-25883;
remote-browser for the CDP gateway changes).

## How to add a new hygiene DoD

1. Add a `step_*` to `tests3/tests/security-hygiene.sh`.
2. Register it in `tests3/registry.yaml` under `type: script`.
3. Add the DoD to `dods.yaml` with the new check id in `evidence.check`.

Nothing else — the aggregator picks it up automatically on next run.
