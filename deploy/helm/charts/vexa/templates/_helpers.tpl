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

{{/* The on-demand bot image the runtime spawns (BROWSER_IMAGE). The bot is published, never built by
this chart. runtime.browserImage is the explicit value; global.imageTag (set) pins the standard repo. */}}
{{- define "vexa.botImage" -}}
{{- if .Values.runtime.browserImage -}}
{{- .Values.runtime.browserImage -}}
{{- else if .Values.global.imageTag -}}
{{- printf "vexaai/vexa-bot:%s" .Values.global.imageTag -}}
{{- else -}}
vexaai/vexa-bot:v012
{{- end -}}
{{- end -}}

{{/* The agent-api image ref (AGENT_IMAGE the runtime spawns workers from). global.imageTag wins. */}}
{{- define "vexa.agentImage" -}}
{{- if .Values.global.imageTag -}}
{{- printf "%s:%s" .Values.agentApi.image.repository .Values.global.imageTag -}}
{{- else -}}
{{- .Values.runtime.agentImage | default (printf "%s:%s" .Values.agentApi.image.repository .Values.agentApi.image.tag) -}}
{{- end -}}
{{- end -}}

{{/* The agent-worker image ref (AGENT_WORKER_IMAGE; the dedicated worker build — core/agent/worker/Dockerfile — NOT the agent-api image). */}}
{{- define "vexa.agentWorkerImage" -}}
{{- if .Values.global.imageTag -}}
{{- printf "vexaai/v012-agent-worker:%s" .Values.global.imageTag -}}
{{- else -}}
{{- .Values.runtime.agentWorkerImage | default "vexaai/v012-agent-worker:v012" -}}
{{- end -}}
{{- end -}}

{{/* The flows-tier image ref (flows-api + flows-worker + flows-mailbox + the ensure-db
initContainer — one image, four entrypoints). Same shape as vexa.botImage: an explicit
flows.image wins, otherwise global.imageTag pins the standard repo, otherwise the chart's own
default tag. It used to be a bare `vexaai/v012-flows:dev` value — a MUTABLE tag, and one the
release's build-once promotion could never pin, so the flows tier alone floated while every other
service moved to the release digest (A12). */}}
{{- define "vexa.flowsImage" -}}
{{- /* BOTH SHAPES, on purpose (0.12.27). `flows.image` is the STRUCTURED {repository, tag} every
other v0.12 component uses — it had to become one for #1537, because the publisher's pin injector
merges {flows: {image: {tag: <version>@sha256:...}}} over these values and merging a map over a
string drops the repository silently. It is ALSO still accepted as a FLAT REF string, which is how
a publisher digest-pins the whole tier in one value; that override wins over everything. Otherwise
the tag follows `global.imageTag` exactly like admin-api, gateway, meeting-api, runtime and
terminal do, so the flows tier can no longer float on a mutable `:dev` while the rest of a release
moves to its digest (A12). One resolver, six call sites. */}}
{{- $img := .Values.flows.image -}}
{{- if and (kindIs "string" $img) (ne ($img | toString) "") -}}
{{- $img -}}
{{- else -}}
{{- $repo := "vexaai/v012-flows" -}}
{{- $tag := "v012" -}}
{{- if .Values.flows.imageRepository -}}{{- $repo = .Values.flows.imageRepository -}}{{- end -}}
{{- if .Values.flows.imageTag -}}{{- $tag = .Values.flows.imageTag -}}{{- end -}}
{{- if kindIs "map" $img -}}
{{- if (get $img "repository") -}}{{- $repo = (get $img "repository") -}}{{- end -}}
{{- if (get $img "tag") -}}{{- $tag = (get $img "tag") -}}{{- end -}}
{{- end -}}
{{- if .Values.global.imageTag -}}{{- $tag = .Values.global.imageTag -}}{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
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
