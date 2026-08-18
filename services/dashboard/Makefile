.PHONY: build push set-latest set-dev set-staging help check-clean builder verify

# DockerHub configuration
DOCKERHUB_USER ?= vexaai
IMAGE_NAME ?= dashboard

# Image tagging: use parent IMAGE_TAG if set, otherwise generate timestamp
IMAGE_TAG ?= $(shell date +%y%m%d-%H%M)
LOCAL_TAG := $(DOCKERHUB_USER)/$(IMAGE_NAME):$(IMAGE_TAG)
LAST_TAG_FILE := .last-tag

# Paths. The Dockerfile sits beside this Makefile but its COPY paths are
# repo-root relative (services/dashboard/…, packages/…, deploy/…), so the build
# context is the repo root two levels up — not the directory make runs in.
HERE := $(dir $(lastword $(MAKEFILE_LIST)))
DOCKERFILE := $(abspath $(HERE)Dockerfile)
CONTEXT := $(abspath $(HERE)../..)

# Provenance. The published image must answer "which commit is this?" from its
# own registry metadata, so the revision is read from git rather than typed.
# A dirty tree cannot be described by a commit sha: the label would name a
# source that does not match what was built, so the build refuses. Override
# with ALLOW_DIRTY=1 for throwaway local images that are never pushed.
GIT_REVISION ?= $(shell git -C $(CONTEXT) rev-parse HEAD)
IMAGE_CREATED ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
IMAGE_SOURCE ?= https://github.com/Vexa-ai/vexa
ALLOW_DIRTY ?= 0

# Attestations require the docker-container driver; the default "docker" driver
# silently drops them, which is how a build record stays on the build host and
# never reaches the registry.
BUILDX_BUILDER ?= vexa-attested
PLATFORMS ?= linux/amd64

PROVENANCE_ARGS := \
	--build-arg VEXA_IMAGE_REVISION=$(GIT_REVISION) \
	--build-arg VEXA_IMAGE_CREATED=$(IMAGE_CREATED) \
	--build-arg VEXA_IMAGE_VERSION=$(IMAGE_TAG) \
	--build-arg VEXA_IMAGE_SOURCE=$(IMAGE_SOURCE)

# Function to set a tag on DockerHub using buildx imagetools
define set_tag
	echo "Setting '$(1)' tag to point to $(2)..."
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):$(1) $(DOCKERHUB_USER)/$(IMAGE_NAME):$(2)
	echo "'$(1)' tag updated to $(2)"
endef

help:
	@echo "Vexa Dashboard Docker Build & Push"
	@echo ""
	@echo "Usage:"
	@echo "  make build                  - Build locally with timestamp tag"
	@echo "  make push                   - Build + push with SLSA provenance and SBOM, update :dev"
	@echo "  make verify [TAG=]          - Read labels + attestations back off the registry"
	@echo "  make set-latest [TAG=]      - Set :latest to specified tag (or last pushed)"
	@echo "  make set-staging [TAG=]     - Set :staging to specified tag (or last pushed)"
	@echo "  make set-dev [TAG=]         - Set :dev to specified tag (or last pushed)"
	@echo ""
	@echo "Prefer CI: .github/workflows/dashboard-image.yml builds the same image"
	@echo "with a GitHub-issued builder identity. This Makefile is the manual path."
	@echo ""
	@echo "Current tag: $(IMAGE_TAG)   revision: $(GIT_REVISION)"

check-clean:
	@if [ "$(ALLOW_DIRTY)" != "1" ] && [ -n "$$(git -C $(CONTEXT) status --porcelain)" ]; then \
		echo "ERROR: working tree is dirty — revision $(GIT_REVISION) would not describe this build."; \
		echo "       Commit first, or pass ALLOW_DIRTY=1 for a local image you will not push."; \
		exit 1; \
	fi

builder:
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || \
		docker buildx create --name $(BUILDX_BUILDER) --driver docker-container >/dev/null

build: check-clean builder
	@echo "Building $(LOCAL_TAG) at revision $(GIT_REVISION)..."
	docker buildx build --builder $(BUILDX_BUILDER) --load \
		-f $(DOCKERFILE) -t $(LOCAL_TAG) $(PROVENANCE_ARGS) $(CONTEXT)
	@echo "$(IMAGE_TAG)" > $(LAST_TAG_FILE)
	@echo "Build complete: $(LOCAL_TAG)"

# Build and push in ONE buildx invocation: the SLSA provenance and SBOM are
# emitted as referrers alongside the manifest as it is pushed. A separate
# `docker push` of a previously-built image cannot carry them — the attestation
# stays in the local build record and dies with the next `buildx prune`.
push: check-clean builder
	@echo "Building + pushing $(LOCAL_TAG) at revision $(GIT_REVISION) with attestations..."
	docker buildx build --builder $(BUILDX_BUILDER) --push \
		--platform $(PLATFORMS) \
		--provenance=mode=max --sbom=true \
		-f $(DOCKERFILE) -t $(LOCAL_TAG) $(PROVENANCE_ARGS) $(CONTEXT)
	@echo "$(IMAGE_TAG)" > $(LAST_TAG_FILE)
	@echo "Push complete: $(LOCAL_TAG)"
	$(call set_tag,dev,$(IMAGE_TAG))

# Read provenance back off the registry — the check that the attestation
# actually rode along instead of staying on this machine.
verify:
	@echo "Labels on $(DOCKERHUB_USER)/$(IMAGE_NAME):$(or $(TAG),$(IMAGE_TAG)):"
	@docker buildx imagetools inspect $(DOCKERHUB_USER)/$(IMAGE_NAME):$(or $(TAG),$(IMAGE_TAG)) \
		--format '{{json .Image}}' | grep -o '"org.opencontainers.image.[a-z]*":"[^"]*"' || \
		echo "  (none — image carries no OCI labels)"
	@echo "Attestations:"
	@docker buildx imagetools inspect $(DOCKERHUB_USER)/$(IMAGE_NAME):$(or $(TAG),$(IMAGE_TAG)) \
		--format '{{json .Provenance}}' | head -c 400 || true
	@echo ""

set-latest:
	@TAG_TO_USE="$(TAG)"; \
	if [ -z "$$TAG_TO_USE" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "ERROR: No TAG specified and .last-tag not found"; exit 1; \
		fi; \
	fi; \
	echo "Setting :latest → $$TAG_TO_USE"; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):latest $(DOCKERHUB_USER)/$(IMAGE_NAME):$$TAG_TO_USE; \
	echo ":latest updated to $$TAG_TO_USE"

set-staging:
	@TAG_TO_USE="$(TAG)"; \
	if [ -z "$$TAG_TO_USE" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "ERROR: No TAG specified and .last-tag not found"; exit 1; \
		fi; \
	fi; \
	echo "Setting :staging → $$TAG_TO_USE"; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):staging $(DOCKERHUB_USER)/$(IMAGE_NAME):$$TAG_TO_USE; \
	echo ":staging updated to $$TAG_TO_USE"

set-dev:
	@TAG_TO_USE="$(TAG)"; \
	if [ -z "$$TAG_TO_USE" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "ERROR: No TAG specified and .last-tag not found"; exit 1; \
		fi; \
	fi; \
	echo "Setting :dev → $$TAG_TO_USE"; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):dev $(DOCKERHUB_USER)/$(IMAGE_NAME):$$TAG_TO_USE; \
	echo ":dev updated to $$TAG_TO_USE"
