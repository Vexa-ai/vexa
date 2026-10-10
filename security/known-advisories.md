# Known advisories — what the CVE scanners are told to accept, and why

The scanners in [`.github/workflows/cve-scanning.yml`](../.github/workflows/cve-scanning.yml) fail on any
vulnerability that is not listed in an allow file. Each allowed advisory is listed here with the reason and
the date it comes back for review. An entry is removed when the dependency that carries it moves.

## Dependencies (OSV-Scanner)

Allow files: [`osv-scanner.toml`](../osv-scanner.toml) (for `pnpm-lock.yaml`),
[`clients/terminal/osv-scanner.toml`](../clients/terminal/osv-scanner.toml) and
[`services/dashboard/osv-scanner.toml`](../services/dashboard/osv-scanner.toml). OSV-Scanner reads the
file beside each lockfile.

| Advisory | Package | Severity | Why it is accepted | Review by |
|---|---|---|---|---|
| [GHSA-hp3w-g68c-fv3c](https://osv.dev/GHSA-hp3w-g68c-fv3c) | `sprintf-js` 1.1.3 | Medium | No fixed release exists. Reached only through `roarr` ← `global-agent` ← `onnxruntime-node`, which formats its own log lines while its postinstall downloads the ONNX runtime; no request data reaches it. | 2026-12-31 |
| [GHSA-238p-pmpm-9mq7](https://osv.dev/GHSA-238p-pmpm-9mq7) | `katex` 0.16.47 | Low | A trust bypass that needs prototype pollution to exist already. Reached through `mermaid` in the terminal's diagram rendering; the fix (0.18.2) is outside mermaid 11's `^0.16` range. | 2026-12-31 |
| [GHSA-vfj7-8cjw-p6xm](https://osv.dev/GHSA-vfj7-8cjw-p6xm) | `braces` 3.0.3 (`services/dashboard`) | High | No fixed release exists. A dev dependency only (`eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch`), run at lint time over the repository's own globs; not in the dashboard image. | 2026-12-31 |

### adm-zip (the advisory the report left open)

[GHSA-vwc7-r8mq-g2x9](https://github.com/advisories/GHSA-vwc7-r8mq-g2x9) (extraction follows destination
symlinks) and six other `adm-zip` advisories are fixed in 0.6.1, published 2026-09-11. The workspace
override in [`pnpm-workspace.yaml`](../pnpm-workspace.yaml) now holds `adm-zip` at `^0.6.1`, and no
advisory is open against it. The exposure was narrow in any case: the only consumer is
`onnxruntime-node`'s postinstall, which unpacks the ONNX runtime archive it downloads itself into its own
package directory at install time. No Vexa code imports `adm-zip`, and nothing extracts a user-supplied
archive with it at runtime.

## Dependency review (pull requests)

[`.github/workflows/dependency-review.yml`](../.github/workflows/dependency-review.yml) fails a pull
request that adds a runtime dependency with a HIGH or CRITICAL advisory, or one licensed outside FINOS
Categories A and B (LGPL is Category X there). Its reviewed exceptions:

- **`typing-extensions`** is PSF-2.0 (its own `license_expression`); GitHub's licence detector reads the
  history section of the PSF licence file and reports GPL-1.0-or-later.
- **libvips behind `sharp`** (`@img/sharp-libvips-*`, and the `@img/sharp-wasm32` / `@img/sharp-win32-*`
  builds that bundle it; LGPL-3.0-or-later) remains in two npm locks outside the pnpm tree:
  `clients/terminal/package-lock.json`, installed by the terminal's build stage and removed from its
  runtime tree, and `services/dashboard/package-lock.json`, the retiring 0.10 dashboard whose `next/image`
  uses it ([`license-exceptions.json`](../license-exceptions.json)). The pnpm tree, and so the bot and
  Lite, load `core/meetings/modules/no-image-backend` instead.

## Third-party images (Trivy)

Allow file: [`.github/trivy-ignore-third-party.yaml`](../.github/trivy-ignore-third-party.yaml), used only for the images
Vexa's deploy surfaces pin ([`scripts/pinned-images.mjs`](../scripts/pinned-images.mjs)), never for
Vexa's own images. Each entry is a fixed CRITICAL or HIGH advisory inside an upstream image, scoped by
package URL to the package it was found in, and expires on 2026-12-31. Produced from a Trivy 0.74.0 scan
on 2026-10-10.

| Image | Role | Findings | Why it is accepted |
|---|---|---|---|
| `postgres:17-alpine` | Compose and Helm database | Go standard library inside `gosu` (1 critical, 24 high) | `gosu` runs once at container start to drop from root before exec'ing postgres and reads no network input. Clears when Docker rebuilds `gosu` with a newer Go; the digest pin moves with it. |
| `versity/versitygw:v1.8.0` | Lite and Compose object storage | Go standard library and `golang.org/x/net` in the binary, Alpine OpenSSL | v1.8.0 is the latest release. Reachable on the stack's networks and, in Compose, on host loopback only. |
| `mcr.microsoft.com/playwright:v1.56.0-noble` | Base of `vexa-lite` and `vexa-bot` | npm's bundled `tar`, `glob`, `minimatch`, `brace-expansion`, `picomatch`, `sigstore`, `pacote`, `ip-address`; Ubuntu OpenSSL and GnuPG | npm runs only while those images build. Moving the base moves the browser the bot joins meetings with, which is a release decision. |
| `litellm/litellm:v1.97.2` | `llm-shim`, off by default (Compose profile) | Python and Wolfi packages in LiteLLM's image | No LiteLLM release scanned that day is free of them; the shim is bumped on purpose with its configuration. |
| `fedirz/faster-whisper-server:latest-cpu` | Lite `LOCAL_STT`, opt-in | Python and Ubuntu packages | The image is no longer maintained upstream (the project continues as speaches). Replacing the sidecar is a separate change. |

`valkey/valkey`, `edoburu/pgbouncer` and `searxng/searxng` had no fixed CRITICAL or HIGH finding.
