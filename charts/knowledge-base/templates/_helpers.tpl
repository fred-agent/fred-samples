{{- define "knowledge-base.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "knowledge-base.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "knowledge-base.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "knowledge-base.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "knowledge-base.selectorLabels" -}}
app.kubernetes.io/name: {{ include "knowledge-base.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "knowledge-base.labels" -}}
helm.sh/chart: {{ include "knowledge-base.chart" . }}
{{ include "knowledge-base.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: knowledge-base
{{- end -}}

{{- define "knowledge-base.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{- define "knowledge-base.configMapName" -}}
{{- printf "%s-config" (include "knowledge-base.fullname" .) -}}
{{- end -}}

{{- define "knowledge-base.secretEnvName" -}}
{{- required "configuration.security.m2m.secret_env_var is required" .Values.configuration.security.m2m.secret_env_var -}}
{{- end -}}

{{- define "knowledge-base.m2mSecretName" -}}
{{- if .Values.m2mSecret.create -}}
{{- default (printf "%s-m2m" (include "knowledge-base.fullname" .)) .Values.m2mSecret.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- required "m2mSecret.existingSecret is required when m2mSecret.create=false" .Values.m2mSecret.existingSecret -}}
{{- end -}}
{{- end -}}
