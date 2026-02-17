.PHONY: build push build-and-push set-latest set-dev setup-builder help

# DockerHub configuration
DOCKERHUB_USER ?= vexaai
IMAGE_NAME ?= vexa-lite
TAG ?= dev

# Multi-arch platforms
PLATFORMS ?= linux/amd64,linux/arm64

# Buildx builder name
BUILDER_NAME ?= vexa-multiarch

# Paths (Makefile is in docker/lite/, Dockerfile is in same dir, context is repo root)
DOCKERFILE := $(shell pwd)/Dockerfile.lite
CONTEXT := $(shell pwd)/../..
LOCAL_TAG := $(IMAGE_NAME):$(TAG)

# Generate unique timestamp tag: YYMMDD-HHMM
UNIQUE_TAG := $(shell date +%y%m%d-%H%M)
UNIQUE_DOCKERHUB_TAG := $(DOCKERHUB_USER)/$(IMAGE_NAME):$(UNIQUE_TAG)
LAST_TAG_FILE := .last-tag

# Function to set a tag on DockerHub using buildx imagetools
define set_tag
	@echo "Setting '$(1)' tag to point to $(2)..."
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):$(1) $(DOCKERHUB_USER)/$(IMAGE_NAME):$(2)
	@echo "'$(1)' tag updated to $(2)"
endef

help:
	@echo "Vexa Lite Docker Build & Push (Multi-Arch)"
	@echo ""
	@echo "Usage:"
	@echo "  make build              - Build locally for current platform (tagged as dev)"
	@echo "  make push               - Build multi-arch (amd64+arm64), push with timestamp tag, update 'dev' tag"
	@echo "  make build-and-push     - Alias for push"
	@echo "  make set-latest [TAG=]  - Set 'latest' tag to specified tag (or last pushed)"
	@echo "  make set-dev [TAG=]     - Set 'dev' tag to specified tag (or last pushed)"
	@echo "  make setup-builder      - Create/bootstrap the multi-arch buildx builder"
	@echo ""
	@echo "Examples:"
	@echo "  make build"
	@echo "  make push"
	@echo "  make set-latest TAG=240115-1430"
	@echo "  make set-latest"
	@echo ""
	@echo "Configuration:"
	@echo "  PLATFORMS=$(PLATFORMS)"

setup-builder:
	@if ! docker buildx inspect $(BUILDER_NAME) >/dev/null 2>&1; then \
		echo "Creating buildx builder '$(BUILDER_NAME)'..."; \
		docker buildx create --name $(BUILDER_NAME) --driver docker-container --bootstrap; \
	else \
		echo "Builder '$(BUILDER_NAME)' already exists"; \
	fi

build:
	@echo "Building $(LOCAL_TAG) for current platform..."
	docker build -f $(DOCKERFILE) -t $(LOCAL_TAG) $(CONTEXT)
	@echo "Build complete: $(LOCAL_TAG)"

push: setup-builder
	@echo "Building multi-arch $(UNIQUE_DOCKERHUB_TAG) for $(PLATFORMS)..."
	docker buildx build \
		--builder $(BUILDER_NAME) \
		--platform $(PLATFORMS) \
		-f $(DOCKERFILE) \
		-t $(UNIQUE_DOCKERHUB_TAG) \
		--push \
		$(CONTEXT)
	@echo "$(UNIQUE_TAG)" > $(LAST_TAG_FILE)
	@echo "Push complete: $(UNIQUE_DOCKERHUB_TAG)"
	$(call set_tag,dev,$(UNIQUE_TAG))

build-and-push: push

set-latest:
	@if [ -z "$(TAG)" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "Error: No TAG specified and $(LAST_TAG_FILE) not found"; exit 1; \
		fi; \
	else \
		TAG_TO_USE=$(TAG); \
	fi; \
	echo "Setting 'latest' tag to point to $$TAG_TO_USE..."; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):latest $(DOCKERHUB_USER)/$(IMAGE_NAME):$$TAG_TO_USE; \
	echo "'latest' tag updated to $$TAG_TO_USE"

set-dev:
	@if [ -z "$(TAG)" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "Error: No TAG specified and $(LAST_TAG_FILE) not found"; exit 1; \
		fi; \
	else \
		TAG_TO_USE=$(TAG); \
	fi; \
	echo "Setting 'dev' tag to point to $$TAG_TO_USE..."; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):dev $(DOCKERHUB_USER)/$(IMAGE_NAME):$$TAG_TO_USE; \
	echo "'dev' tag updated to $$TAG_TO_USE"
