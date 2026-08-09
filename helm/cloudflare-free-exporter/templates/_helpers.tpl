{{- define "cloudflare-free-exporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cloudflare-free-exporter.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "cloudflare-free-exporter.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "cloudflare-free-exporter.labels" -}}
app.kubernetes.io/name: {{ include "cloudflare-free-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "cloudflare-free-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cloudflare-free-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "cloudflare-free-exporter.secretName" -}}
{{- if .Values.cloudflare.create -}}
{{- include "cloudflare-free-exporter.fullname" . -}}
{{- else -}}
{{- required "cloudflare.existingSecret is required when cloudflare.create is false" .Values.cloudflare.existingSecret -}}
{{- end -}}
{{- end -}}
