- **Identity answers an agent worker's token only to a caller that declares it reads one (#1784).**
  `/internal/validate` verifies a worker's delegation token (`vxd_…`) only when the caller sends
  `X-Vexa-Internal-Accepts-Delegation: 1`; to any other caller it is `401 Invalid token`, like a key
  nobody holds. The gateway and flows-api send it. A gateway older than v0.13.2 does not, so upgrade
  the gateway, MCP, admin-api, agent-api and flows-api together — see
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
