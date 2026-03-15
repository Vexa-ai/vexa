{{/*
Helpers
*/}}

{{ define "vexa-lite.name" -}}
{{ default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{ end -}}

{{ define "vexa-lite.fullname" -}}
{{ if .Values.fullnameOverride -}}
{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{ else -}}
{{ $name := include "vexa-lite.name" . -}}
{{ printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{ end -}}
{{ end -}}

{{ define "vexa-lite.labels" }}
app.kubernetes.io/name: {{ include "vexa-lite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{ end }}

{{ define "vexa-lite.selectorLabels" }}
app.kubernetes.io/name: {{ include "vexa-lite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{ end }}

{{ define "vexa-lite.serviceAccountName" -}}
{{ if .Values.serviceAccount.create -}}
{{ default (include "vexa-lite.fullname" .) .Values.serviceAccount.name -}}
{{ else -}}
{{ default "default" .Values.serviceAccount.name -}}
{{ end -}}
{{ end -}}

{{ define "vexa-lite.configmapName" -}}
{{ include "vexa-lite.fullname" . }}-config
{{ end -}}

{{ define "vexa-lite.secretName" -}}
{{ if .Values.vexa.existingSecret -}}
{{ .Values.vexa.existingSecret }}
{{ else -}}
{{ include "vexa-lite.fullname" . }}-secret
{{ end -}}
{{ end }}
