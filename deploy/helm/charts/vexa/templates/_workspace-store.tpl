{{/*
The workspace store backend (WS-S3). agentApi.workspaces.backend:
  volume (default) — the agent-workspaces PVC is the truth, mounted into agent-api and every worker.
  s3               — an S3-compatible bucket is the single durable truth; agent-api and workers keep
                     only an emptyDir cache, and no write is acknowledged before the bucket has it.

vexa.workspaceStore resolves agentApi.workspaces with defaults, as YAML for `fromYaml`:
  {{- $ws := include "vexa.workspaceStore" . | fromYaml }}
nil-safe for --reuse-values from a chart that had no `backend` key.
*/}}
{{- define "vexa.workspaceStore" -}}
{{- $w := (.Values.agentApi | default dict).workspaces | default dict -}}
{{- $s := $w.s3 | default dict -}}
{{- $ca := $s.caBundle | default dict -}}
backend: {{ $w.backend | default "volume" | toString | trim | lower | quote }}
s3: {{ eq ($w.backend | default "volume" | toString | trim | lower) "s3" }}
keepVolumeClaim: {{ ne (toString $s.keepVolumeClaim) "false" }}
cacheSizeLimit: {{ $w.cacheSizeLimit | default "" | quote }}
workerCacheSize: {{ $w.workerCacheSize | default "" | quote }}
cacheMaxBytes: {{ $w.cacheMaxBytes | default "0" | toString | quote }}
endpoint: {{ $s.endpoint | default "" | toString | trim | quote }}
bucket: {{ $s.bucket | default "" | toString | trim | quote }}
prefix: {{ $s.prefix | default "workspaces/" | toString | trim | quote }}
region: {{ $s.region | default "" | toString | trim | quote }}
pathStyle: {{ ne (toString $s.forcePathStyle) "false" }}
existingSecret: {{ $s.existingSecret | default "" | toString | trim | quote }}
accessKeyIdKey: {{ $s.accessKeyIdKey | default "AWS_ACCESS_KEY_ID" | quote }}
secretAccessKeyKey: {{ $s.secretAccessKeyKey | default "AWS_SECRET_ACCESS_KEY" | quote }}
caConfigMap: {{ $ca.configMapName | default "" | quote }}
caSecret: {{ $ca.secretName | default "" | quote }}
caKey: {{ $ca.key | default "ca.crt" | quote }}
migrate: {{ eq (toString (($s.migrate | default dict).enabled)) "true" }}
{{- end -}}

{{/* Render-time guards: each stops install/upgrade with what to set. */}}
{{- define "vexa.workspaceStore.validate" -}}
{{- if .Values.agentApi.enabled -}}
{{- $ws := include "vexa.workspaceStore" . | fromYaml -}}
{{- if not (has $ws.backend (list "volume" "s3")) -}}
{{- fail (printf "agentApi.workspaces.backend must be volume or s3 (got %q)" $ws.backend) -}}
{{- end -}}
{{- if $ws.s3 -}}
{{- $missing := list -}}
{{- range $k := list "bucket" "existingSecret" -}}
{{- if not (get $ws $k) -}}
{{- $missing = append $missing (printf "agentApi.workspaces.s3.%s" $k) -}}
{{- end -}}
{{- end -}}
{{- if $missing -}}
{{- fail (printf "agentApi.workspaces.backend=s3 is incomplete, missing: %s. Name the workspaces bucket and a Secret holding a static key scoped to it (keys %s and %s)." (join ", " $missing) $ws.accessKeyIdKey $ws.secretAccessKeyKey) -}}
{{- end -}}
{{- if and $ws.endpoint (not (regexMatch "^https?://[^/?#\\s]+(/\\S*)?$" (lower $ws.endpoint))) -}}
{{- fail (printf "agentApi.workspaces.s3.endpoint must be a full URL starting with https:// or http:// (got %q)" $ws.endpoint) -}}
{{- end -}}
{{- if and $ws.caConfigMap $ws.caSecret -}}
{{- fail "agentApi.workspaces.s3.caBundle: set configMapName or secretName, not both" -}}
{{- end -}}
{{- $rec := include "vexa.s3" . | fromYaml -}}
{{- if and $rec.bucket (eq $rec.bucket $ws.bucket) (eq ($rec.endpoint | trimSuffix "/") ($ws.endpoint | trimSuffix "/")) -}}
{{- fail "agentApi.workspaces.s3.bucket must not be the recordings bucket (storage.s3.bucket): workspaces get a bucket of their own" -}}
{{- end -}}
{{- if and $rec.existingSecret (eq $rec.existingSecret $ws.existingSecret) -}}
{{- fail "agentApi.workspaces.s3.existingSecret must not be the recordings key (storage.s3.existingSecret): use a key scoped to the workspaces bucket" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The bucket's env for the migration Job (whatever the backend). agent-api spells the same keys
     inline in deployment-agent-api.yaml, where gate:config-contract reads them. */}}
{{- define "vexa.workspaceStore.bucketEnv" -}}
{{- $ws := include "vexa.workspaceStore" . | fromYaml -}}
- name: VEXA_WORKSPACE_S3_ENDPOINT
  value: {{ $ws.endpoint | quote }}
- name: VEXA_WORKSPACE_S3_BUCKET
  value: {{ $ws.bucket | quote }}
- name: VEXA_WORKSPACE_S3_PREFIX
  value: {{ $ws.prefix | quote }}
- name: VEXA_WORKSPACE_S3_REGION
  value: {{ $ws.region | quote }}
- name: VEXA_WORKSPACE_S3_PATH_STYLE
  value: {{ $ws.pathStyle | quote }}
- name: VEXA_WORKSPACE_S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $ws.existingSecret | quote }}
      key: {{ $ws.accessKeyIdKey | quote }}
- name: VEXA_WORKSPACE_S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $ws.existingSecret | quote }}
      key: {{ $ws.secretAccessKeyKey | quote }}
{{- if or $ws.caConfigMap $ws.caSecret }}
- name: VEXA_WORKSPACE_S3_CA_BUNDLE
  value: {{ printf "/run/vexa-workspace-s3-ca/%s" $ws.caKey | quote }}
{{- end }}
{{- end -}}

{{/* The CA bundle volumeMount / volume for the s3 backend (empty when none is named). */}}
{{- define "vexa.workspaceStore.caMount" -}}
{{- $ws := include "vexa.workspaceStore" . | fromYaml -}}
{{- if and (or $ws.s3 $ws.migrate) (or $ws.caConfigMap $ws.caSecret) }}
- name: workspace-s3-ca
  mountPath: /run/vexa-workspace-s3-ca
  readOnly: true
{{- end }}
{{- end -}}
{{- define "vexa.workspaceStore.caVolume" -}}
{{- $ws := include "vexa.workspaceStore" . | fromYaml -}}
{{- if and (or $ws.s3 $ws.migrate) $ws.caConfigMap }}
- name: workspace-s3-ca
  configMap:
    name: {{ $ws.caConfigMap | quote }}
{{- else if and (or $ws.s3 $ws.migrate) $ws.caSecret }}
- name: workspace-s3-ca
  secret:
    secretName: {{ $ws.caSecret | quote }}
{{- end }}
{{- end -}}
