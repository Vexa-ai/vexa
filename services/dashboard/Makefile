.PHONY: build push set-latest set-dev set-staging help

# DockerHub configuration
DOCKERHUB_USER ?= vexaai
IMAGE_NAME ?= dashboard

# Image tagging: use parent IMAGE_TAG if set, otherwise generate timestamp
IMAGE_TAG ?= $(shell date +%y%m%d-%H%M)
LOCAL_TAG := $(DOCKERHUB_USER)/$(IMAGE_NAME):$(IMAGE_TAG)
LAST_TAG_FILE := .last-tag

# Paths (Makefile and Dockerfile are in the same directory, context is repo root)
DOCKERFILE := $(shell pwd)/Dockerfile
CONTEXT := $(shell pwd)

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
	@echo "  make push                   - Build, push timestamp tag, update :dev"
	@echo "  make set-latest [TAG=]      - Set :latest to specified tag (or last pushed)"
	@echo "  make set-staging [TAG=]     - Set :staging to specified tag (or last pushed)"
	@echo "  make set-dev [TAG=]         - Set :dev to specified tag (or last pushed)"
	@echo ""
	@echo "Current tag: $(IMAGE_TAG)"

build:
	@echo "Building $(LOCAL_TAG)..."
	docker build -f $(DOCKERFILE) -t $(LOCAL_TAG) $(CONTEXT)
	@echo "$(IMAGE_TAG)" > $(LAST_TAG_FILE)
	@echo "Build complete: $(LOCAL_TAG)"

push: build
	@echo "Pushing $(LOCAL_TAG)..."
	docker push $(LOCAL_TAG)
	@echo "Push complete: $(LOCAL_TAG)"
	$(call set_tag,dev,$(IMAGE_TAG))

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
