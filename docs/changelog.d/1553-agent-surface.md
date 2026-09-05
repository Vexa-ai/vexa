- **The agent surface is unchanged (#1553).** `/agent/*` keeps the seven routes it served at
  0.12.26, now declared in `core/agent/routes.v1.json` like every other domain. Compose keeps its
  default `AGENT_API_URL`; a deployment that leaves the variable unset serves no agent surface and
  answers `404` there.
