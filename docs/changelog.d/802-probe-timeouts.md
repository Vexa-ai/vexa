- **helm chart: probe timeouts raised to 5s across app services (#802).** Liveness/readiness/startup
  probes previously inherited Kubernetes' 1-second default `timeoutSeconds`; on a busy single-event-loop
  service a healthy pod can hold `/health` past 1s, and the liveness kill turned load into a restart
  storm (observed in hosted production on meeting-api: pods serving 200s probe-killed every ~10 min).
  All HTTP-probed app deployments (meeting-api, gateway, admin-api, runtime, agent-api, terminal) now
  declare `timeoutSeconds: 5`.
