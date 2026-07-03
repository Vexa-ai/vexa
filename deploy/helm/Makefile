# deploy/helm — the v0.12 control-plane Helm chart (lite/compose/helm trio: this is `helm`).
# Static gates need no cluster; `smoke` stands the chart up on a local k3s node.

CHART        ?= charts/vexa
RELEASE      ?= vexa
NAMESPACE    ?= vexa
VALUES       ?= $(CHART)/values-test.yaml
# k3s writes a root-only kubeconfig; smoke targets read it via sudo. Override for other clusters.
KUBECONFIG   ?= /etc/rancher/k3s/k3s.yaml
COMPOSE      ?= ../compose/docker-compose.yml
IMAGES        = vexaai/v012-gateway vexaai/v012-admin-api vexaai/v012-meeting-api \
                vexaai/v012-runtime vexaai/v012-agent-api

## lint — helm lint (default + values-test)
lint:
	helm lint $(CHART)
	helm lint $(CHART) -f $(VALUES)

## template — render to stdout with values-test
template:
	helm template $(RELEASE) $(CHART) -n $(NAMESPACE) -f $(VALUES)

## test — the static gate:helm proof (structure + lint + render assertions, no cluster)
test:
	bash tests/test_helm_lint.sh
	bash tests/test_template.sh

## build — build the 5 control-plane images (:dev) via the compose build
build:
	IMAGE_TAG=dev docker compose -f $(COMPOSE) build gateway admin-api meeting-api runtime agent-api

## k3s-import — load locally-built :dev images into the k3s node's containerd (no registry push)
k3s-import:
	@for img in $(IMAGES); do \
	  echo "importing $$img:dev"; \
	  docker save "$$img:dev" | sudo k3s ctr images import - ; \
	done

## install — helm upgrade --install onto the cluster (KUBECONFIG)
install:
	sudo KUBECONFIG=$(KUBECONFIG) helm upgrade --install $(RELEASE) $(CHART) \
	  -n $(NAMESPACE) --create-namespace -f $(VALUES) --wait --timeout 5m

## status — pods + rollout
status:
	sudo KUBECONFIG=$(KUBECONFIG) kubectl -n $(NAMESPACE) get pods,svc

## smoke — full local proof: build → import → install → wait → /health → (teardown via `make down`)
smoke: build k3s-import install status

## down — uninstall the release + namespace
down:
	-sudo KUBECONFIG=$(KUBECONFIG) helm uninstall $(RELEASE) -n $(NAMESPACE)
	-sudo KUBECONFIG=$(KUBECONFIG) kubectl delete namespace $(NAMESPACE) --wait=false

.PHONY: lint template test build k3s-import install status smoke down
