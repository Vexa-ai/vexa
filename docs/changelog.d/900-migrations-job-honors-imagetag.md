- **Helm migrations Job now honors `global.imageTag` (#900).** A pinned-tag deploy with
  `migrations.enabled=true` previously ran the schema-convergence Job from the rolling `v012`
  image instead of the deployed release tag — a schema/code skew risk. The Job now resolves its
  tag with the same precedence the component Deployments use (`global.imageTag` wins, falling back
  to the `migrations`/`meetingApi` image tag). See [Kubernetes deployment](/deployment-kubernetes).
