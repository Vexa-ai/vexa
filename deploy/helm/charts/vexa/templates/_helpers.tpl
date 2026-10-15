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

{{- /* The chart's redis requires a password on its default user; a consumer of this URL carries the
REDIS_PASSWORD env entry (vexa.redisPasswordEnv) BEFORE it, which Kubernetes expands into $(REDIS_PASSWORD). */ -}}
{{- define "vexa.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://:$(REDIS_PASSWORD)@%s.%s.svc.%s:%d/0" (include "vexa.componentName" (list . "redis")) .Release.Namespace .Values.global.clusterDomain (.Values.redis.service.port | int) -}}
{{- else -}}
{{- required "redisConfig.url is required when redis.enabled=false" .Values.redisConfig.url -}}
{{- end -}}
{{- end -}}

{{- define "vexa.redisPasswordEnv" -}}
{{- if .Values.redis.enabled }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "vexa.adminTokenSecretName" . }}
      key: REDIS_PASSWORD
{{- end }}
{{- end -}}

{{/*
The workspace store's path inside agent-api and inside every worker — agent-api's VEXA_WORKSPACES_DIR,
its store mountPath, and the runtime's VEXA_WORKSPACE_MOUNT_TARGET. They must be equal, or every
dispatch is refused, so all three are rendered from here.
*/}}
{{- define "vexa.workspacesDir" -}}
/workspaces
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

{{/*
Where flows' operator key (VEXA_FLOWS_API_KEY) comes from when it is a Secret you manage rather than
a chart value: `flows.existingSecret`, else `secrets.existingSecretName` when `flows.apiKey` is empty.
Empty ⇒ the chart renders `flows.apiKey` into its own flows Secret, as it always has. A template-only
render (GitOps) names a Secret here so the key never sits in values.
*/}}
{{- define "vexa.flowsApiKeyExistingSecret" -}}
{{- if and .Values.flows.existingSecret .Values.flows.apiKey -}}
{{- fail "flows.apiKey and flows.existingSecret are both set — keep one: the key in values, or the Secret that carries VEXA_FLOWS_API_KEY" -}}
{{- end -}}
{{- if .Values.flows.existingSecret -}}
{{- .Values.flows.existingSecret -}}
{{- else if and (not .Values.flows.apiKey) .Values.secrets.existingSecretName -}}
{{- .Values.secrets.existingSecretName -}}
{{- end -}}
{{- end -}}

{{/* The Secret every consumer of VEXA_FLOWS_API_KEY reads it from. */}}
{{- define "vexa.flowsApiKeySecretName" -}}
{{- include "vexa.flowsApiKeyExistingSecret" . | default (include "vexa.componentName" (list . "flows")) -}}
{{- end -}}

{{/* flows' own containers take the flows Secret by envFrom; when the key lives in a Secret you
manage, they read it from there by name instead (an explicit env entry wins over envFrom). */}}
{{- define "vexa.flowsApiKeyEnv" -}}
{{- with include "vexa.flowsApiKeyExistingSecret" . -}}
- name: VEXA_FLOWS_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . | quote }}
      key: VEXA_FLOWS_API_KEY
{{- end -}}
{{- end -}}

{{/*
THE BOT MAILBOX (flows.mail) — invite-by-email against any IMAP server, Gmail being only the preset.
Nil-safe throughout: `helm upgrade --reuse-values` renders with the PREVIOUS chart's values, which
have no imap/smtp/ca blocks at all.

vexa.flowsMailboxEnabled — "true" when the flows-mailbox Deployment renders. An explicit bool in
flows.mail.enabled wins; unset, it is on exactly when an address is set (what an install that set
one already ran).
*/}}
{{- define "vexa.flowsMailboxEnabled" -}}
{{- $m := .Values.flows.mail | default dict -}}
{{- if kindIs "bool" $m.enabled -}}
{{- if $m.enabled -}}true{{- end -}}
{{- else if $m.address -}}true{{- end -}}
{{- end -}}

{{/* The Secret carrying the mailbox passwords, when the operator manages it; refuses both. */}}
{{- define "vexa.flowsMailExistingSecret" -}}
{{- $m := .Values.flows.mail | default dict -}}
{{- if and $m.existingSecret $m.appPassword -}}
{{- fail "flows.mail.appPassword and flows.mail.existingSecret are both set — keep one: the password in values, or the Secret that carries VEXA_MAIL_APP_PASSWORD" -}}
{{- end -}}
{{- $m.existingSecret | default "" -}}
{{- end -}}

{{/* Where the CA bundle is mounted in every flows container, when one is named. */}}
{{- define "vexa.flowsMailCaPath" -}}/etc/vexa/mail-ca/ca.pem{{- end -}}

{{- define "vexa.flowsMailCa" -}}
{{- $ca := (.Values.flows.mail | default dict).ca | default dict -}}
{{- if and $ca.configMapName $ca.secretName -}}
{{- fail "flows.mail.ca: set configMapName or secretName, not both" -}}
{{- end -}}
configMap: {{ $ca.configMapName | default "" | quote }}
secret: {{ $ca.secretName | default "" | quote }}
key: {{ $ca.key | default "ca.crt" | quote }}
{{- end -}}

{{/*
vexa.flowsMailEnv — the mail settings every flows container reads (the worker SENDS, the mailbox
READS, flows-api reports the capability). Rendered as explicit env entries, which win over the
flows Secret's envFrom. SMTP falls back to terminal.mail when flows.mail.smtp.host is empty: the
deployment has ONE relay and an operator configures it once.
*/}}
{{- define "vexa.flowsMailEnv" -}}
{{- $m := .Values.flows.mail | default dict -}}
{{- $imap := $m.imap | default dict -}}
{{- $own := $m.smtp | default dict -}}
{{- $term := (.Values.terminal | default dict).mail | default dict -}}
{{- $smtp := $own -}}
{{- if not $own.host -}}
{{- $smtp = dict "host" $term.smtpHost "port" $term.smtpPort "user" $term.user "secure" $term.secure "tlsInsecure" $term.tlsInsecure "tls" $own.tls -}}
{{- end -}}
{{- $ca := include "vexa.flowsMailCa" . | fromYaml -}}
{{- $existing := include "vexa.flowsMailExistingSecret" . -}}
- name: VEXA_MAIL_IMAP_HOST
  value: {{ $imap.host | default "" | toString | quote }}
- name: VEXA_MAIL_IMAP_PORT
  value: {{ $imap.port | default "" | toString | quote }}
- name: VEXA_MAIL_IMAP_TLS
  value: {{ $imap.tls | default "" | toString | quote }}
- name: VEXA_MAIL_IMAP_USER
  value: {{ $imap.user | default "" | toString | quote }}
- name: VEXA_MAIL_IMAP_FOLDER
  value: {{ $imap.folder | default "" | toString | quote }}
- name: VEXA_FLOWS_MAIL_AUTHSERV_ID
  value: {{ $m.authservId | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_HOST
  value: {{ $smtp.host | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_PORT
  value: {{ $smtp.port | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_TLS
  value: {{ $smtp.tls | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_USER
  value: {{ $smtp.user | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_SECURE
  value: {{ $smtp.secure | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_TLS_INSECURE
  value: {{ $smtp.tlsInsecure | default "" | toString | quote }}
- name: VEXA_MAIL_SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $existing | default (include "vexa.adminTokenSecretName" .) | quote }}
      key: VEXA_MAIL_SMTP_PASSWORD
      optional: true
{{- /* THE RELAY PASSWORD BY REFERENCE (PRD 0.13.4 S2): broker:mail/<name>, read by flows from the
credential broker with the broker's sensors key, mounted below. Set, it is the only source. */}}
{{- if $own.passwordRef }}
{{- if not (include "vexa.credentialBrokerEnabled" .) }}
{{- fail "flows.mail.smtp.passwordRef needs the credential broker (agentApi.enabled and credentialBroker.enabled)" -}}
{{- end }}
{{- if not (regexMatch "^broker:mail/[a-z0-9][a-z0-9_.-]{0,62}$" $own.passwordRef) }}
{{- fail "flows.mail.smtp.passwordRef must be broker:mail/<name> (lowercase name)" -}}
{{- end }}
- name: VEXA_MAIL_SMTP_PASSWORD_REF
  value: {{ $own.passwordRef | quote }}
- name: VEXA_FLOWS_SENSORS_BROKER_URL
  value: {{ include "vexa.credentialBrokerUrl" . | quote }}
- name: VEXA_FLOWS_SENSORS_KEY_FILE
  value: /run/vexa-connections/sensors/key
{{- end }}
{{- /* The recipient allowlist (PRD 0.13.4 S2): set, mail to anybody else is diverted, never sent. */}}
- name: VEXA_MAIL_RECIPIENT_ALLOW
  value: {{ $m.recipientAllow | default "" | toString | quote }}
{{- if $existing }}
- name: VEXA_MAIL_APP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $existing | quote }}
      key: VEXA_MAIL_APP_PASSWORD
{{- end }}
{{- if or $ca.configMap $ca.secret }}
- name: VEXA_MAIL_IMAP_CA_FILE
  value: {{ include "vexa.flowsMailCaPath" . | quote }}
- name: VEXA_MAIL_SMTP_CA_FILE
  value: {{ include "vexa.flowsMailCaPath" . | quote }}
{{- end }}
{{- end -}}

{{/* The CA bundle's volume and mount (nothing when no bundle is named). */}}
{{- define "vexa.flowsMailCaVolume" -}}
{{- $ca := include "vexa.flowsMailCa" . | fromYaml -}}
{{- if ((.Values.flows.mail | default dict).smtp | default dict).passwordRef }}
- name: flows-broker-key
  secret:
    secretName: {{ include "vexa.credentialBrokerKeys" . | quote }}
    defaultMode: 0440
    items: [{key: sensors.key, path: sensors/key}]
{{- end }}
{{- if $ca.configMap }}
- name: mail-ca
  configMap:
    name: {{ $ca.configMap | quote }}
    items: [{key: {{ $ca.key | quote }}, path: ca.pem}]
{{- else if $ca.secret }}
- name: mail-ca
  secret:
    secretName: {{ $ca.secret | quote }}
    items: [{key: {{ $ca.key | quote }}, path: ca.pem}]
{{- end }}
{{- end -}}

{{- define "vexa.flowsMailCaMount" -}}
{{- $ca := include "vexa.flowsMailCa" . | fromYaml -}}
{{- if ((.Values.flows.mail | default dict).smtp | default dict).passwordRef }}
- name: flows-broker-key
  mountPath: /run/vexa-connections
  readOnly: true
{{- end }}
{{- if or $ca.configMap $ca.secret }}
- name: mail-ca
  mountPath: /etc/vexa/mail-ca
  readOnly: true
{{- end }}
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
{{/*
The instance's administrators for admin-api's VEXA_ADMIN_EMAILS: `adminApi.adminEmails`. Before
admin-api read this list the terminal did, and the chart told operators to set it in
`terminal.extraEnv`; an install that still sets it there is carried over here rather than silently
losing its admins on upgrade.
*/}}
{{- define "vexa.adminEmails" -}}
{{- $emails := .Values.adminApi.adminEmails | default "" -}}
{{- if not $emails -}}
{{- range (.Values.terminal.extraEnv | default list) -}}
{{- if and (kindIs "map" .) (eq (.name | default "") "VEXA_ADMIN_EMAILS") -}}
{{- $emails = .value | default "" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $emails -}}
{{- end -}}

{{/* The default tag of every image this chart deploys: the release the chart belongs to,
`v<appVersion>` — an immutable tag, never a moving pointer such as `latest`. A component's own
`image.tag` or `global.imageTag` overrides it. */}}
{{- define "vexa.releaseTag" -}}
{{- printf "v%s" .Chart.AppVersion -}}
{{- end -}}

{{- define "vexa.botImage" -}}
{{- if .Values.runtime.browserImage -}}
{{- .Values.runtime.browserImage -}}
{{- else if .Values.global.imageTag -}}
{{- printf "vexaai/vexa-bot:%s" .Values.global.imageTag -}}
{{- else -}}
{{- printf "vexaai/vexa-bot:%s" (include "vexa.releaseTag" .) -}}
{{- end -}}
{{- end -}}

{{/* The agent-api image ref (AGENT_IMAGE the runtime spawns workers from). global.imageTag wins. */}}
{{- define "vexa.agentImage" -}}
{{- if .Values.global.imageTag -}}
{{- printf "%s:%s" .Values.agentApi.image.repository .Values.global.imageTag -}}
{{- else -}}
{{- .Values.runtime.agentImage | default (printf "%s:%s" .Values.agentApi.image.repository (.Values.agentApi.image.tag | default (include "vexa.releaseTag" .))) -}}
{{- end -}}
{{- end -}}

{{/* The agent-worker image ref (AGENT_WORKER_IMAGE; the dedicated worker build — core/agent/worker/Dockerfile — NOT the agent-api image).
global.imageTag replaces the TAG only: the repository is runtime.agentWorkerImage's, so an install that
mirrors the images (Harbor, a GitLab registry) and pins one tag gets its workers from the mirror, like
every other image. It used to be a literal Docker Hub `agent-worker:<tag>` reference, so a mirrored install
spawned every chat worker from Docker Hub and, offline, none started. */}}
{{- define "vexa.agentWorkerImage" -}}
{{- $img := .Values.runtime.agentWorkerImage | default (printf "vexaai/vexa-agent-worker:%s" (include "vexa.releaseTag" .)) -}}
{{- if .Values.global.imageTag -}}
{{- printf "%s:%s" (regexReplaceAll "(@sha256:[a-f0-9]+|:[^:/]+)$" $img "") .Values.global.imageTag -}}
{{- else -}}
{{- $img -}}
{{- end -}}
{{- end -}}

{{/* The flows-tier image ref (flows-api + flows-worker + flows-mailbox + the ensure-db
initContainer — one image, four entrypoints). Same shape as vexa.botImage: an explicit
flows.image wins, otherwise global.imageTag pins the standard repo, otherwise the chart's own
default tag. It used to be a bare `vexaai/vexa-flows:dev` value — a MUTABLE tag, and one the
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
{{- $repo := "vexaai/vexa-flows" -}}
{{- $tag := include "vexa.releaseTag" . -}}
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

{{/*
The database passwords published in this repository — the bundled database refuses them as an
explicit value, and the postgres-password hook moves an install off them. The same list compose's
postgres and Lite's entrypoint refuse.
*/}}
{{- define "vexa.publishedDbPasswords" -}}
["postgres","password","vexa-internal-secret","lite-internal-secret","changeme","change-me","CHANGE-ME","default","secret"]
{{- end -}}

{{- define "vexa.postgresCredentialsSecretName" -}}
{{- if .Values.database.existingSecret -}}
{{- .Values.database.existingSecret -}}
{{- else if .Values.postgres.enabled -}}
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

{{/*
Object storage — storage.s3, the operator's own S3-compatible bucket (the only place recordings go).

vexa.s3 resolves storage.s3 with its defaults applied IN THE TEMPLATE, as YAML for `fromYaml`:
  {{- $s3 := include "vexa.s3" . | fromYaml }}
In-template defaults, nil-safe on a missing `storage` key, because `helm upgrade --reuse-values`
renders with the PREVIOUS chart's default values: from a MinIO-era release there is no `storage`
block at all, and a nil dereference would bury the guard's message under a template error.
*/}}
{{- define "vexa.s3" -}}
{{- $s3 := (.Values.storage | default dict).s3 | default dict -}}
{{- $ca := $s3.caBundle | default dict -}}
endpoint: {{ $s3.endpoint | default "" | toString | trim | quote }}
bucket: {{ $s3.bucket | default "" | toString | trim | quote }}
region: {{ $s3.region | default "us-east-1" | toString | trim | quote }}
pathStyle: {{ ne (toString $s3.forcePathStyle) "false" }}
existingSecret: {{ $s3.existingSecret | default "" | toString | trim | quote }}
accessKeyIdKey: {{ $s3.accessKeyIdKey | default "AWS_ACCESS_KEY_ID" | quote }}
secretAccessKeyKey: {{ $s3.secretAccessKeyKey | default "AWS_SECRET_ACCESS_KEY" | quote }}
caConfigMap: {{ $ca.configMapName | default "" | quote }}
caSecret: {{ $ca.secretName | default "" | quote }}
caKey: {{ $ca.key | default "ca.crt" | quote }}
{{- end -}}

{{/*
vexa.storage.validate — render-time guards, called from templates/validate-storage.yaml. Each one
stops install/upgrade/template with a message that names what to set; none of them falls back to
storage inside a pod.
  1. a stale `minio.enabled: true` — the built-in MinIO is gone from this chart;
  2. storage.s3 without endpoint, bucket or existingSecret (every missing key named at once);
  3. an endpoint that is not a full http(s) URL, a region that could not be a region, or a CA
     bundle named twice;
  4. meetingApi.extraEnv setting a storage variable the chart now sets from storage.s3, or an AWS
     variable that overrides the mounted client config — Kubernetes accepts duplicate env names,
     and botocore prefers environment settings and shared-credentials profiles to the config.
*/}}
{{- define "vexa.storage.validate" -}}
{{- $minio := .Values.minio | default dict -}}
{{- if eq (toString $minio.enabled) "true" -}}
{{- fail (printf "minio.enabled=true is no longer supported: this chart no longer runs MinIO, whose images can no longer be pulled. Recordings are stored in your own S3-compatible bucket: set storage.s3.endpoint, storage.s3.bucket and storage.s3.existingSecret, and remove minio.enabled from your values (with --reuse-values, which keeps the previous chart's defaults, pass --set minio.enabled=false). If this release ran the built-in MinIO, copy its objects to your bucket before you upgrade; the upgrade does not delete the PVC data-%s-0. Steps: https://docs.vexa.ai/deployment-kubernetes#upgrading-from-the-built-in-minio" (include "vexa.componentName" (list . "minio"))) -}}
{{- end -}}
{{- if .Values.meetingApi.enabled -}}
{{- $s3 := include "vexa.s3" . | fromYaml -}}
{{- $missing := list -}}
{{- range $k := list "endpoint" "bucket" "existingSecret" -}}
{{- if not (get $s3 $k) -}}
{{- $missing = append $missing (printf "storage.s3.%s" $k) -}}
{{- end -}}
{{- end -}}
{{- if $missing -}}
{{- fail (printf "storage.s3 is incomplete, missing: %s. Vexa stores recordings in your own S3-compatible bucket and this chart runs no object store. Set storage.s3.endpoint (a full URL, e.g. https://s3.eu-central-1.amazonaws.com), storage.s3.bucket (an existing bucket) and storage.s3.existingSecret (a Secret holding the keys %s and %s). Upgrading a release that ran the built-in MinIO? Copy its objects to your bucket first: https://docs.vexa.ai/deployment-kubernetes#upgrading-from-the-built-in-minio" (join ", " $missing) $s3.accessKeyIdKey $s3.secretAccessKeyKey) -}}
{{- end -}}
{{- if not (regexMatch "^https?://[^/?#\\s]+(/\\S*)?$" (lower $s3.endpoint)) -}}
{{- fail (printf "storage.s3.endpoint must be a full URL starting with https:// or http:// (got %q)" $s3.endpoint) -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9._-]+$" $s3.region) -}}
{{- fail (printf "storage.s3.region must be a region name such as us-east-1 (got %q)" $s3.region) -}}
{{- end -}}
{{- if and $s3.caConfigMap $s3.caSecret -}}
{{- fail "storage.s3.caBundle: set configMapName or secretName, not both" -}}
{{- end -}}
{{- $chartSet := list "S3_ENDPOINT" "S3_ACCESS_KEY" "S3_SECRET_KEY" "MINIO_ENDPOINT" "MINIO_SECURE" "MINIO_BUCKET" "MINIO_ACCESS_KEY" "MINIO_SECRET_KEY" "STORAGE_BACKEND" "AWS_CONFIG_FILE" "AWS_DEFAULT_REGION" "AWS_CA_BUNDLE" "AWS_PROFILE" "AWS_SHARED_CREDENTIALS_FILE" -}}
{{- $clash := list -}}
{{- range .Values.meetingApi.extraEnv -}}
{{- if has .name $chartSet -}}
{{- $clash = append $clash .name -}}
{{- end -}}
{{- end -}}
{{- if $clash -}}
{{- fail (printf "meetingApi.extraEnv sets %s, which the chart now sets from storage.s3. Put the value in storage.s3 and remove it from meetingApi.extraEnv." (join ", " $clash)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Pod-level and container-level securityContext, both gated by `global.securityContext.deliver`.

Emitted as the WHOLE key (`securityContext:` included) so that `deliver: false` renders no key at
all rather than an empty map — on OpenShift the absence is the point: `restricted-v2` injects the
context and rejects a spec that supplies a conflicting one. `deliver` is stripped from the rendered
container context (it is our switch, not a Kubernetes field).

A values file that replaces `global.securityContext` wholesale without naming `deliver` keeps the
old behaviour: missing key == true, so no existing install changes shape on upgrade.

Call with the root context and the indent of the key itself:
    {{- include "vexa.podSecurityContext" . | nindent 6 }}          # under spec.template.spec
    {{- include "vexa.containerSecurityContext" . | nindent 10 }}   # under a container
*/}}
{{- define "vexa.securityContextDeliver" -}}
{{- $sc := .Values.global.securityContext | default dict -}}
{{- if hasKey $sc "deliver" }}{{ $sc.deliver }}{{ else }}true{{ end }}
{{- end -}}

{{- define "vexa.podSecurityContext" -}}
{{- if eq (include "vexa.securityContextDeliver" .) "true" -}}
securityContext:
  {{- toYaml (.Values.global.podSecurityContext | default dict) | nindent 2 }}
{{- end -}}
{{- end -}}

{{/*
The pod securityContext for a workload whose image runs as the unprivileged uid 10001 (its
Dockerfile USER: gateway, admin-api, meeting-api, mcp, terminal, flows): the global pod context plus
runAsNonRoot, that uid and gid, and fsGroup 10001 so a Secret it mounts group-readable (the
gateway's signing key, 0440) is readable by it. The global values win where both are set (an
OpenShift range, say). Same `deliver` gate as vexa.podSecurityContext.
*/}}
{{- define "vexa.nonRootPodSecurityContext" -}}
{{- if eq (include "vexa.securityContextDeliver" .) "true" -}}
securityContext:
  {{- toYaml (merge (deepCopy (.Values.global.podSecurityContext | default dict)) (dict "runAsNonRoot" true "runAsUser" 10001 "runAsGroup" 10001 "fsGroup" 10001)) | nindent 2 }}
{{- end -}}
{{- end -}}

{{- define "vexa.containerSecurityContext" -}}
{{- if eq (include "vexa.securityContextDeliver" .) "true" -}}
{{- $sc := omit (.Values.global.securityContext | default dict) "deliver" -}}
securityContext:
  {{- toYaml $sc | nindent 2 }}
{{- end -}}
{{- end -}}

{{/* Connections (ADR-0040): the broker deploys with agent-api, never without it. */}}
{{- define "vexa.credentialBrokerEnabled" -}}
{{- if and .Values.agentApi.enabled .Values.credentialBroker.enabled -}}true{{- end -}}
{{- end -}}

{{/* Bot accounts (ADR-0049): meeting-api keeps saved signed-in browser profiles in the broker. */}}
{{- define "vexa.botAccountsEnabled" -}}
{{- if and (include "vexa.credentialBrokerEnabled" .) .Values.credentialBroker.botAccounts -}}true{{- end -}}
{{- end -}}

{{/* The Secret holding agent.key, human.key, git.key, bots.key, store.key (and google-client-secret). */}}
{{- define "vexa.credentialBrokerKeys" -}}
{{- .Values.credentialBroker.existingSecret | default (include "vexa.componentName" (list . "credential-broker-keys")) -}}
{{- end -}}

{{- define "vexa.credentialBrokerUrl" -}}
{{- printf "http://%s:%v" (include "vexa.componentName" (list . "credential-broker")) .Values.credentialBroker.service.port -}}
{{- end -}}

{{/* The OAuth callback the broker sends Google: explicit, else the terminal's https public URL. */}}
{{- define "vexa.credentialBrokerRedirect" -}}
{{- if .Values.credentialBroker.productRedirect -}}
{{- .Values.credentialBroker.productRedirect -}}
{{- else if hasPrefix "https://" (.Values.terminal.publicUrl | default "") -}}
{{- printf "%s/api/auth/callback/google" (trimSuffix "/" .Values.terminal.publicUrl) -}}
{{- end -}}
{{- end -}}

{{- /*
vexa.captureSignalDefault — VEXA_CAPTURE_SIGNAL_DEFAULT for admin-api and meeting-api, from
diagnostics.captureSignal: "true" or "false", nothing else. Default "false": a bot spawned by this
chart tapes no captured signal (raw audio, captions, speaker events) unless the operator turns it
on. A value that is neither stops the render rather than reaching a service that would refuse to
boot on it.
*/ -}}
{{- define "vexa.captureSignalDefault" -}}
{{- $raw := (.Values.diagnostics | default dict).captureSignal -}}
{{- $v := ternary "false" (toString $raw | lower) (kindIs "invalid" $raw) -}}
{{- if not (has $v (list "true" "false")) -}}
{{- fail (printf "diagnostics.captureSignal must be \"true\" or \"false\" (got %q)" (toString $raw)) -}}
{{- end -}}
{{- $v -}}
{{- end -}}

{{/*
The session store (ADR-0048). vexa.agentSessions resolves agentApi.sessions with defaults, nil-safe
for --reuse-values from a chart without the block; `active` is true only when the store's own scoped
key is named (the chart creates no bucket). The role's password Secret is generated once and kept.
*/}}
{{- define "vexa.agentSessions" -}}
{{- $a := (.Values.agentApi | default dict).sessions | default dict -}}
{{- $s3 := $a.s3 | default dict -}}
{{- $gen := include "vexa.componentName" (list . "agent-sessions-db") -}}
dbUser: {{ $a.dbUser | default "vexa_agent_sessions" | quote }}
dbSecret: {{ $a.existingSecret | default $gen | quote }}
dbSecretGenerated: {{ not $a.existingSecret }}
bucket: {{ $s3.bucket | default "vexa-sessions" | toString | trim | quote }}
existingSecret: {{ $s3.existingSecret | default "" | toString | trim | quote }}
accessKeyIdKey: {{ $s3.accessKeyIdKey | default "AWS_ACCESS_KEY_ID" | quote }}
secretAccessKeyKey: {{ $s3.secretAccessKeyKey | default "AWS_SECRET_ACCESS_KEY" | quote }}
active: {{ and .Values.agentApi.enabled (ne ($s3.existingSecret | default "" | toString | trim) "") }}
{{- end -}}

{{- define "vexa.agentSessions.validate" -}}
{{- $ss := include "vexa.agentSessions" . | fromYaml -}}
{{- if $ss.active -}}
{{- $s3 := include "vexa.s3" . | fromYaml -}}
{{- if eq $ss.bucket $s3.bucket -}}
{{- fail (printf "agentApi.sessions.s3.bucket is the recordings bucket (%s). The session store needs its own bucket on storage.s3's endpoint, reached with a key scoped to it (ADR-0048)." $s3.bucket) -}}
{{- end -}}
{{- if eq $ss.existingSecret $s3.existingSecret -}}
{{- fail "agentApi.sessions.s3.existingSecret names the recordings key Secret (storage.s3.existingSecret). The session store needs its own key pair, scoped to its own bucket (ADR-0048)." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Corporate TLS inspection: global.extraCA (a ConfigMap OR a Secret holding the PEM root CA, under
`key`). When set, every Vexa workload Pod — the chart's Deployments and Jobs, and through the runtime
(RUNTIME_K8S_EXTRA_CA) every bot and agent worker Pod it spawns — gets:
  * the PEM mounted read-only at /etc/vexa/extra-ca/ca.crt;
  * an init container that writes the image's system bundle plus that PEM into an emptyDir,
    mounted read-only at /etc/vexa/ca-bundle/ca-bundle.crt (no root, no writable root filesystem);
  * SSL_CERT_FILE, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE and GIT_SSL_CAINFO pointing at that bundle,
    NODE_EXTRA_CA_CERTS and VEXA_EXTRA_CA_FILE at the PEM (the bot imports it into Chromium's NSS
    store at start), and AWS_CA_BUNDLE at the bundle unless storage.s3.caBundle names S3's own CA.
Unset renders nothing: every helper below is empty, so the chart is byte-identical.
Call with the root context. vexa.extraCA.mounts / .env / .volumes / .initContainer emit LIST ITEMS
(no key) at column 0; indent them with nindent where they are included.
*/}}
{{- define "vexa.extraCA" -}}
{{- $c := (.Values.global | default dict).extraCA | default dict -}}
configMap: {{ $c.configMap | default "" | toString | trim | quote }}
secret: {{ $c.secret | default "" | toString | trim | quote }}
key: {{ $c.key | default "ca.crt" | toString | trim | quote }}
{{- end -}}

{{- define "vexa.extraCA.enabled" -}}
{{- $c := include "vexa.extraCA" . | fromYaml -}}
{{- if and $c.configMap $c.secret -}}
{{- fail "global.extraCA: name a configMap or a secret, not both" -}}
{{- end -}}
{{- if or $c.configMap $c.secret -}}true{{- end -}}
{{- end -}}

{{/* AWS_CA_BUNDLE only when S3 has no CA of its own (that one is set through the client config,
which an AWS_CA_BUNDLE in the environment would override). */}}
{{- define "vexa.extraCA.aws" -}}
{{- $s3 := include "vexa.s3" . | fromYaml -}}
{{- if not (or $s3.caConfigMap $s3.caSecret) -}}true{{- end -}}
{{- end -}}

{{/* The JSON the runtime reads (RUNTIME_K8S_EXTRA_CA) to give every spawned Pod the same. */}}
{{- define "vexa.extraCA.runtimeJson" -}}
{{- $c := include "vexa.extraCA" . | fromYaml -}}
{{- dict "configMap" $c.configMap "secret" $c.secret "key" $c.key "awsCaBundle" (eq (include "vexa.extraCA.aws" .) "true") | toJson -}}
{{- end -}}

{{- define "vexa.extraCA.volumes" -}}
{{- if include "vexa.extraCA.enabled" . -}}
{{- $c := include "vexa.extraCA" . | fromYaml }}
- name: vexa-extra-ca
  {{- if $c.configMap }}
  configMap:
    name: {{ $c.configMap | quote }}
  {{- else }}
  secret:
    secretName: {{ $c.secret | quote }}
  {{- end }}
    defaultMode: 0444
    items:
      - { key: {{ $c.key | quote }}, path: ca.crt }
- name: vexa-ca-bundle
  emptyDir:
    sizeLimit: 8Mi
{{- end -}}
{{- end -}}

{{- define "vexa.extraCA.mounts" -}}
{{- if include "vexa.extraCA.enabled" . }}
- name: vexa-extra-ca
  mountPath: /etc/vexa/extra-ca
  readOnly: true
- name: vexa-ca-bundle
  mountPath: /etc/vexa/ca-bundle
  readOnly: true
{{- end -}}
{{- end -}}

{{- define "vexa.extraCA.env" -}}
{{- if include "vexa.extraCA.enabled" . }}
- name: SSL_CERT_FILE
  value: /etc/vexa/ca-bundle/ca-bundle.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/vexa/ca-bundle/ca-bundle.crt
- name: CURL_CA_BUNDLE
  value: /etc/vexa/ca-bundle/ca-bundle.crt
- name: GIT_SSL_CAINFO
  value: /etc/vexa/ca-bundle/ca-bundle.crt
- name: NODE_EXTRA_CA_CERTS
  value: /etc/vexa/extra-ca/ca.crt
- name: VEXA_EXTRA_CA_FILE
  value: /etc/vexa/extra-ca/ca.crt
{{- if include "vexa.extraCA.aws" . }}
- name: AWS_CA_BUNDLE
  value: /etc/vexa/ca-bundle/ca-bundle.crt
{{- end }}
{{- end -}}
{{- end -}}

{{/* The bundle writer. Its image is global.extraCA.image, else the runtime image (Debian, has the
system bundle and a shell). It runs as whatever uid the platform assigns and writes only the
emptyDir. A PEM with no certificate in it stops the Pod rather than starting it without the CA. */}}
{{- define "vexa.extraCA.script" -}}
set -eu
out=/etc/vexa/ca-bundle/ca-bundle.crt
grep -q 'BEGIN CERTIFICATE' /etc/vexa/extra-ca/ca.crt || { echo 'global.extraCA: the key holds no PEM certificate' >&2; exit 1; }
: > "$out"
for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
  if [ -s "$f" ]; then cat "$f" >> "$out"; echo >> "$out"; break; fi
done
cat /etc/vexa/extra-ca/ca.crt >> "$out"
echo "ca-bundle: $(grep -c 'BEGIN CERTIFICATE' "$out") certificates"
{{- end -}}

{{- define "vexa.extraCA.initContainer" -}}
{{- if include "vexa.extraCA.enabled" . -}}
{{- $c := (.Values.global | default dict).extraCA | default dict }}
- name: vexa-ca-bundle
  {{- include "vexa.containerSecurityContext" . | nindent 2 }}
  image: {{ $c.image | default (printf "%s:%s" .Values.runtime.image.repository (.Values.global.imageTag | default .Values.runtime.image.tag | default (include "vexa.releaseTag" .))) | quote }}
  imagePullPolicy: {{ .Values.global.imagePullPolicy }}
  command: ["/bin/sh", "-c", {{ include "vexa.extraCA.script" . | quote }}]
  volumeMounts:
    - name: vexa-extra-ca
      mountPath: /etc/vexa/extra-ca
      readOnly: true
    - name: vexa-ca-bundle
      mountPath: /etc/vexa/ca-bundle
  resources:
    {{- toYaml ($c.resources | default (dict "requests" (dict "cpu" "10m" "memory" "16Mi") "limits" (dict "cpu" "100m" "memory" "64Mi"))) | nindent 4 }}
{{- end -}}
{{- end -}}
