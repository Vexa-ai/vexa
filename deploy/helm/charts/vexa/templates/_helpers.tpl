{{/*
Common template helpers
*/}}

{{ define "vexa.name" -}}
{{ default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{ end -}}

{{ define "vexa.fullname" -}}
{{ if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "vexa.name" . -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "vexa.labels" -}}
app.kubernetes.io/name: {{ include "vexa.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "vexa.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vexa.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "vexa.componentName" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- printf "%s-%s" (include "vexa.fullname" $root) $component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vexa.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s.%s.svc.%s:%d/0" (include "vexa.componentName" (list . "redis")) .Release.Namespace .Values.global.clusterDomain (.Values.redis.service.port | int) -}}
{{- else -}}
{{- required "redisConfig.url is required when redis.enabled=false" .Values.redisConfig.url -}}
{{- end -}}
{{- end -}}

{{- define "vexa.redisHost" -}}
{{- if .Values.redis.enabled -}}
{{- printf "%s.%s.svc.%s" (include "vexa.componentName" (list . "redis")) .Release.Namespace .Values.global.clusterDomain -}}
{{- else -}}
{{- required "redisConfig.host is required when redis.enabled=false" .Values.redisConfig.host -}}
{{- end -}}
{{- end -}}

{{- define "vexa.redisPort" -}}
{{- if .Values.redis.enabled -}}
{{- .Values.redis.service.port | int -}}
{{- else -}}
{{- required "redisConfig.port is required when redis.enabled=false" .Values.redisConfig.port -}}
{{- end -}}
{{- end -}}

{{- define "vexa.dbHost" -}}
{{- if .Values.postgres.enabled -}}
{{- include "vexa.componentName" (list . "postgres") -}}
{{- else -}}
{{- required "database.host is required when postgres.enabled=false" .Values.database.host -}}
{{- end -}}
{{- end -}}

{{- /*
  vexa.dbHostEffective — the host every service SHOULD point at for DB.
  When pgbouncer.enabled=true, routes through the pgbouncer Service.
  Otherwise falls through to vexa.dbHost (direct Postgres). PgBouncer's
  own Deployment bypasses this helper and uses vexa.dbHost directly to
  avoid pointing at itself.
*/ -}}
{{- define "vexa.dbHostEffective" -}}
{{- if .Values.pgbouncer.enabled -}}
{{- include "vexa.componentName" (list . "pgbouncer") -}}
{{- else -}}
{{- include "vexa.dbHost" . -}}
{{- end -}}
{{- end -}}

{{- define "vexa.dbPortEffective" -}}
{{- if .Values.pgbouncer.enabled -}}
{{- .Values.pgbouncer.service.port | default 5432 -}}
{{- else -}}
{{- .Values.database.port -}}
{{- end -}}
{{- end -}}

{{- define "vexa.adminTokenSecretName" -}}
{{- if .Values.secrets.existingSecretName -}}
{{- .Values.secrets.existingSecretName -}}
{{- else -}}
{{- include "vexa.componentName" (list . "secrets") -}}
{{- end -}}
{{- end -}}

{{/*
vexa.imageRef — the ONE image-reference rule (#1006).

Call:
  include "vexa.imageRef" (dict "field"      "<values path, quoted back in errors>"
                                "reference"  <explicit full reference, or "">
                                "digest"     <"sha256:<64 hex>", or "">
                                "repository" <repository>
                                "tag"        <tag>)

Precedence — three legs, checked in this order, and nothing else participates:

  1. `reference` non-empty  → emitted VERBATIM. An operator who names a full reference gets
     exactly that reference; a global tag NEVER rewrites it. This is the leg mirror-only
     clusters depend on: before #1006 two of the three spawned-workload references silently
     ignored it and rendered a Docker Hub reference, so a cluster with no public egress could
     not run the product no matter what it configured.
  2. `digest` non-empty     → "<repository>@<digest>".
  3. otherwise              → "<repository>:<tag>" — the legacy path, unchanged. The CALLER
     decides whether global.imageTag supplies that tag.

Fails CLOSED — `helm template` errors and nothing renders — on an ambiguous or malformed
identity, instead of silently ranking one input over another:

  - `reference` AND `digest` both set: two identities declared, no defensible winner;
  - a `digest` that is not exactly sha256:<64 lowercase hex> (truncated, uppercase, non-sha256);
  - a `reference` containing whitespace;
  - an empty repository on leg 2 or 3, or an empty tag on leg 3.

Silence here would mean an operator who typoed a digest deploys a mutable tag believing they
pinned bytes. See docs/docs/deployment-kubernetes.mdx § Running from a private registry mirror.
*/}}
{{- define "vexa.imageRef" -}}
{{- $field := .field -}}
{{- $ref := .reference | default "" | toString | trim -}}
{{- $digest := .digest | default "" | toString | trim -}}
{{- $repo := .repository | default "" | toString | trim -}}
{{- $tag := .tag | default "" | toString | trim -}}
{{- if and $ref $digest -}}
{{- fail (printf "INVALID image identity for %s: an explicit full reference (%q) and a digest (%q) are both set. Declare exactly one — the chart refuses to guess which bytes you meant." $field $ref $digest) -}}
{{- end -}}
{{- if $ref -}}
{{- if regexMatch "[[:space:]]" $ref -}}
{{- fail (printf "INVALID image reference for %s: %q contains whitespace." $field $ref) -}}
{{- end -}}
{{- $ref -}}
{{- else if $digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail (printf "INVALID image digest for %s: %q. Expected exactly sha256:<64 lowercase hex> — a truncated, uppercase or non-sha256 digest is refused rather than silently falling back to a mutable tag." $field $digest) -}}
{{- end -}}
{{- if not $repo -}}
{{- fail (printf "INVALID image identity for %s: a digest is set but the repository is empty." $field) -}}
{{- end -}}
{{- printf "%s@%s" $repo $digest -}}
{{- else -}}
{{- if not $repo -}}
{{- fail (printf "INVALID image identity for %s: no reference, no digest, and an empty repository." $field) -}}
{{- end -}}
{{- if not $tag -}}
{{- fail (printf "INVALID image identity for %s: no reference, no digest, and an empty tag for repository %q." $field $repo) -}}
{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/* The on-demand bot image the runtime spawns (BROWSER_IMAGE). The bot is published, never built
by this chart. Identity per vexa.imageRef; global.imageTag only ever supplies the tag leg. */}}
{{- define "vexa.botImage" -}}
{{- include "vexa.imageRef" (dict
      "field" "runtime.browserImage"
      "reference" .Values.runtime.browserImage
      "digest" .Values.runtime.browserImageDigest
      "repository" .Values.runtime.browserImageRepository
      "tag" (.Values.global.imageTag | default .Values.runtime.browserImageTag)) -}}
{{- end -}}

{{/* The agent-api image ref (AGENT_IMAGE the runtime spawns workers from). Its composed leg falls
back to the agent-api Deployment's own repository/tag, so mirroring `agentApi.image` keeps carrying
the spawned copy with it. */}}
{{- define "vexa.agentImage" -}}
{{- include "vexa.imageRef" (dict
      "field" "runtime.agentImage"
      "reference" .Values.runtime.agentImage
      "digest" .Values.runtime.agentImageDigest
      "repository" (.Values.runtime.agentImageRepository | default .Values.agentApi.image.repository)
      "tag" (.Values.global.imageTag | default .Values.runtime.agentImageTag | default .Values.agentApi.image.tag)) -}}
{{- end -}}

{{/* The agent-worker image ref (AGENT_WORKER_IMAGE; the dedicated worker build — core/agent/worker/Dockerfile — NOT the agent-api image). */}}
{{- define "vexa.agentWorkerImage" -}}
{{- include "vexa.imageRef" (dict
      "field" "runtime.agentWorkerImage"
      "reference" .Values.runtime.agentWorkerImage
      "digest" .Values.runtime.agentWorkerImageDigest
      "repository" .Values.runtime.agentWorkerImageRepository
      "tag" (.Values.global.imageTag | default .Values.runtime.agentWorkerImageTag)) -}}
{{- end -}}

{{/* The MinIO client image the post-install bucket-init Job runs. Same one rule, deliberately NOT
wired to global.imageTag: it is a third-party image, not part of the Vexa release set. */}}
{{- define "vexa.minioClientImage" -}}
{{- include "vexa.imageRef" (dict
      "field" "minio.mc"
      "reference" .Values.minio.mc.reference
      "digest" .Values.minio.mc.digest
      "repository" .Values.minio.mc.image.repository
      "tag" .Values.minio.mc.image.tag) -}}
{{- end -}}

{{- define "vexa.postgresCredentialsSecretName" -}}
{{- if .Values.postgres.enabled -}}
{{- .Values.postgres.credentialsSecretName | default "postgres-credentials" -}}
{{- else -}}
{{- required "postgres.credentialsSecretName must name a pre-existing Secret when postgres.enabled=false (keys: POSTGRES_PASSWORD, POSTGRES_USER, POSTGRES_DB)" .Values.postgres.credentialsSecretName -}}
{{- end -}}
{{- end -}}

{{/*
vexa.topologySpreadConstraints — render pod topology spread constraints for a component.

Call:  include "vexa.topologySpreadConstraints" (list $root $componentValues "component-name")
  - $root           = the template root context (.)
  - $componentValues = that component's values map (e.g. .Values.gateway)
  - "component-name" = the value of its app.kubernetes.io/component label (e.g. "gateway")

Per-component `.topologySpreadConstraints` wins over `global.topologySpreadConstraints`
(same override shape as replicaCount/resources). Each constraint that omits `labelSelector`
gets the component's OWN pod selector injected — name + instance + component — so the default
meaning is "spread THIS component's replicas across the topology", which is the part users get
wrong when they hand-write it. A constraint that carries its own labelSelector is rendered
verbatim. Renders NOTHING when neither global nor per-component constraints are set (empty
default is byte-identical to a chart without the field).
*/}}
{{- define "vexa.topologySpreadConstraints" -}}
{{- $root := index . 0 -}}
{{- $componentValues := index . 1 -}}
{{- $component := index . 2 -}}
{{- $constraints := $componentValues.topologySpreadConstraints | default $root.Values.global.topologySpreadConstraints -}}
{{- if $constraints -}}
{{- $selector := dict "matchLabels" (dict "app.kubernetes.io/name" (include "vexa.name" $root) "app.kubernetes.io/instance" $root.Release.Name "app.kubernetes.io/component" $component) -}}
topologySpreadConstraints:
{{- range $constraints }}
{{- if hasKey . "labelSelector" }}
  -{{ toYaml . | nindent 4 }}
{{- else }}
  -{{ toYaml (merge (deepCopy .) (dict "labelSelector" $selector)) | nindent 4 }}
{{- end }}
{{- end }}
{{- end -}}
{{- end -}}

{{- define "vexa.deploymentStrategy" -}}
{{/*
v0.10.5.3 Pack H — zero-downtime rolling update.

Pre-fix: maxSurge: 0, maxUnavailable: 1. With replicaCount: 1, this killed
the OLD pod before creating the NEW pod, causing 502s during any image
bump (e.g. the v0.10.5.2 cycle outage where dashboard + webapp went 502
because new image tags didn't exist on the registry — old pods were
already killed by the time helm upgrade tried to create the new pods).

Post-fix: maxSurge: 1, maxUnavailable: 0. NEW pod is created first;
helm waits until it's Ready before killing the OLD. With --atomic --wait
on the helm upgrade call (release-helm-upgrade-safe Make target),
failed image pulls auto-rollback without ever exposing the outage.

Works on replicaCount=1 (1 old -> 1 old + 1 new -> 1 new) and
replicaCount>1 (rolling progresses one extra at a time).
*/}}
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
{{- end -}}

{{/*
v0.10.5 Pack C.5 — Redis durability paired invariant.

AOF (appendonly + appendfsync) is the per-write durability mechanism.
`stop-writes-on-bgsave-error: no` allows writes to continue when the
snapshot mechanism fails (block-volume hiccup, disk-full, fsync stall) —
which is non-blocking when AOF is on. Setting `stop-writes-on-bgsave-error: yes`
WITHOUT `appendonly: yes` would create a write-loss window: Redis would
accept writes that aren't durable anywhere if BGSAVE fails. Refuse to render.

The 2026-04-21 redis-storage-cascade incident was triggered by exactly
this anti-pattern: BGSAVE failed, default `stop-writes-on-bgsave-error: yes`
froze writes for 46 min. With AOF + bgsave-error: no, BGSAVE failures
become non-blocking. Industry-standard Redis-as-stream-buffer config.
*/}}
{{- define "vexa.validateRedisDurability" -}}
{{- $aof := .Values.redis.durability.appendonly | default "yes" -}}
{{- $bgsaveBlocks := .Values.redis.durability.stopWritesOnBgsaveError | default "no" -}}
{{- if and (eq $bgsaveBlocks "yes") (ne $aof "yes") -}}
{{- required "INVALID redis.durability config: stopWritesOnBgsaveError=yes requires appendonly=yes (paired AOF + BGSAVE durability invariant — see v0.10.5 Pack C.5). Without AOF, blocking writes on BGSAVE failure means writes that arrive while BGSAVE is failing have no durable record anywhere." "" -}}
{{- end -}}
{{- end -}}
