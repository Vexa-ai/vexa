- **Security floor: adm-zip bumped to 0.6.0 via pnpm override (#773).** Closes Dependabot high
  GHSA-xcpc-8h2w-3j85 (crafted-ZIP 4 GB allocation in adm-zip <0.6.0), which rode in through
  onnxruntime-node's install-time unpack under `@vexa/mixed-pipeline`. Exposure was
  postinstall-only, but the resolution layer now excludes the vulnerable range entirely.
