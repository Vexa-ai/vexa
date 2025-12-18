.PHONY: build push build-and-push help

# DockerHub configuration
DOCKERHUB_USER ?= vexaai
IMAGE_NAME ?= vexa-lite
TAG ?= latest

# Paths (Makefile is in docker/lite/, Dockerfile is in same dir, context is repo root)
DOCKERFILE := $(shell pwd)/Dockerfile.lite
CONTEXT := $(shell pwd)/../..
LOCAL_TAG := $(IMAGE_NAME):$(TAG)
DOCKERHUB_TAG := $(DOCKERHUB_USER)/$(IMAGE_NAME):$(TAG)

help:
	@echo "Vexa Lite Docker Build & Push"
	@echo ""
	@echo "Usage:"
	@echo "  make build              - Build the Docker image locally"
	@echo "  make push               - Push the image to DockerHub (requires docker login)"
	@echo "  make build-and-push     - Build and push in one command"
	@echo ""
	@echo "Variables:"
	@echo "  DOCKERHUB_USER=$(DOCKERHUB_USER)  - DockerHub username"
	@echo "  IMAGE_NAME=$(IMAGE_NAME)           - Image name"
	@echo "  TAG=$(TAG)                        - Image tag"
	@echo ""
	@echo "Examples:"
	@echo "  make build TAG=v1.0.0"
	@echo "  make push DOCKERHUB_USER=myuser"
	@echo "  make build-and-push TAG=latest"

build:
	@echo "📦 Building $(LOCAL_TAG)..."
	@echo "   Dockerfile: $(DOCKERFILE)"
	@echo "   Context: $(CONTEXT)"
	docker build -f $(DOCKERFILE) -t $(LOCAL_TAG) $(CONTEXT)
	@echo "✅ Build complete: $(LOCAL_TAG)"

push: build
	@echo "🚀 Pushing $(DOCKERHUB_TAG) to DockerHub..."
	@echo "   Make sure you're logged in: docker login"
	docker tag $(LOCAL_TAG) $(DOCKERHUB_TAG)
	docker push $(DOCKERHUB_TAG)
	@echo "✅ Push complete: $(DOCKERHUB_TAG)"

build-and-push: push
	@echo "✅ Build and push complete!"

