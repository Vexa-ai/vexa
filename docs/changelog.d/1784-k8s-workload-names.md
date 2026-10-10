- **Kubernetes: a chat whose id the cluster could not name now starts (#1784).** The runtime named a
  spawned Pod `vexa-<workload id>` as is. A chat id carries its session, and an onboarding chat's
  session (`scaffold-…`) has capitals and runs past 63 characters, so Kubernetes refused the Pod and
  the first-visit chat never started an agent. Such an id now gets a valid name, with a hash so ids
  never collide, and the id itself rides the Pod's `runtime.workload_id` annotation, which re-adoption
  reads. Ids that were already valid keep their names. The Docker backend does the same for the
  characters Docker refuses, and Lite writes each workload's log as one file inside its log
  directory, whatever the chat's session holds.
