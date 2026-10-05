#!/bin/sh
# A SimCloud managed-Kubernetes node (k3s). K3S_ROLE=server (default) or agent.
#   server: SIMCLOUD_CLUSTER=<project>/<env>/<name>, K3S_TOKEN, SIMCLOUD_K8S_AUDIT_TOKEN
#   agent:  K3S_TOKEN, K3S_SERVER_HOST (default k8s)
# The audit token travels in the webhook path: client-go never sends credentials over plain http.
# Common: NODE_NAME, SIMCLOUD_ZONE, SIMCLOUD_URL (default http://simcloud:7400)
set -eu
ROLE="${K3S_ROLE:-server}"
SERVER_HOST="${K3S_SERVER_HOST:-k8s}"
NODE="${NODE_NAME:-node-1}"
ZONE="${SIMCLOUD_ZONE:-zone-1}"
: "${K3S_TOKEN:?K3S_TOKEN must be set}"

if [ "$ROLE" = agent ]; then
  exec /bin/k3s agent --server "https://${SERVER_HOST}:6443" --token "$K3S_TOKEN" --node-name "$NODE" \
    --node-label "topology.kubernetes.io/zone=${ZONE}" "$@"
fi

: "${SIMCLOUD_CLUSTER:?SIMCLOUD_CLUSTER must be <project>/<env>/<name>}"
: "${SIMCLOUD_K8S_AUDIT_TOKEN:?SIMCLOUD_K8S_AUDIT_TOKEN must be set}"
SC="${SIMCLOUD_URL:-http://simcloud:7400}"
mkdir -p /etc/simcloud /k8s-state
cat > /etc/simcloud/authn-webhook.yaml <<CFG
apiVersion: v1
kind: Config
clusters:
- name: simcloud
  cluster:
    server: ${SC}/k8s/v1/clusters/${SIMCLOUD_CLUSTER}/tokenreview
users:
- name: apiserver
  user: {}
contexts:
- name: default
  context: {cluster: simcloud, user: apiserver}
current-context: default
CFG
cat > /etc/simcloud/audit-webhook.yaml <<CFG
apiVersion: v1
kind: Config
clusters:
- name: simcloud
  cluster:
    server: ${SC}/k8s/v1/clusters/${SIMCLOUD_CLUSTER}/audit/${SIMCLOUD_K8S_AUDIT_TOKEN}
users:
- name: apiserver
  user: {}
contexts:
- name: default
  context: {cluster: simcloud, user: apiserver}
current-context: default
CFG
chmod 600 /etc/simcloud/*.yaml

exec /bin/k3s server --node-name "$NODE" --node-label "topology.kubernetes.io/zone=${ZONE}" \
  --token "$K3S_TOKEN" --tls-san "$SERVER_HOST" --disable=traefik --secrets-encryption \
  --write-kubeconfig /k8s-state/kubeconfig.yaml --write-kubeconfig-mode 644 \
  --kube-apiserver-arg=authentication-token-webhook-config-file=/etc/simcloud/authn-webhook.yaml \
  --kube-apiserver-arg=authentication-token-webhook-cache-ttl=10s \
  --kube-apiserver-arg=audit-policy-file=/etc/simcloud-k8s/audit-policy.yaml \
  --kube-apiserver-arg=audit-webhook-config-file=/etc/simcloud/audit-webhook.yaml \
  --kube-apiserver-arg=audit-webhook-batch-max-wait=1s \
  "$@"
