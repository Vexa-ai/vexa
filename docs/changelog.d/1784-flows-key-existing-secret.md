- **Helm: flows' operator key can come from a Secret you manage (#1784).** Set `flows.existingSecret`,
  or leave `flows.apiKey` empty with `secrets.existingSecretName`. Then flows and the MCP edge read
  `VEXA_FLOWS_API_KEY` from that Secret, so a `helm template` render (ArgoCD) can turn flows on
  without the key in values.
