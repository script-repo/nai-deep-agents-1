{{- define "nda.fullname" -}}{{ .Release.Name }}-nai-agents{{- end -}}
{{- define "nda.labels" -}}
app.kubernetes.io/name: nai-agents
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "nda.selector" -}}
app.kubernetes.io/name: nai-agents
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
