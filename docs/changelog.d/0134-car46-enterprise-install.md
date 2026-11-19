- **Kubernetes: one section for enterprise and OpenShift installs.** [Enterprise and OpenShift installs](/deployment-kubernetes#enterprise-and-openshift-installs)
  lists the namespace rights the person running `helm install` needs (no cluster-scoped object is
  rendered), the OpenShift profile and the SCC meeting bots need, `global.extraCA` (including
  OpenShift's injected trusted-CA bundle), and the settings that change on upgrade: OIDC providers
  without `email_verified`, `flows.mail.authservId`, bot sign-in hosts, English bot accounts, the
  per-producer fact-seal keys, `VEXA_MODEL_PROXY_SECRET`, the one-time database restart, the private
  write `503` while admin-api is down, and Zoom bots turning incoming video off.
- **`global.extraCA` reaches the model proxy, signals and the workspace-store migration Job** as well
  as every other Vexa Pod and every Pod the runtime spawns. The runtime refuses at start a
  Secret-sourced CA key that names a model credential or a control-plane secret, since that key is
  mounted into every workload.
