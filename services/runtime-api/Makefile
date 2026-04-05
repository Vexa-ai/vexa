.PHONY: build up down ps logs test test-unit test-integration health clean

# Build the runtime-api image
build:
	docker compose build

# Start runtime-api + Redis
up: build
	docker compose up -d
	@echo "Waiting for runtime-api to be healthy..."
	@timeout 30 sh -c 'until curl -sf http://localhost:8090/health > /dev/null 2>&1; do sleep 1; done' \
		&& echo "runtime-api is up: http://localhost:8090" \
		|| (echo "ERROR: runtime-api did not start in 30s"; docker compose logs runtime-api; exit 1)

# Stop everything
down:
	docker compose down -v

# Status
ps:
	docker compose ps

# Logs
logs:
	docker compose logs -f

# Health check
health:
	@curl -sf http://localhost:8090/health | python3 -m json.tool

# Run all tests
test: test-unit test-integration

# Unit tests (no infrastructure needed)
test-unit:
	pip install -e ".[dev]" 2>/dev/null || pip install -e .
	pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires running service: make up first)
test-integration: up
	pytest tests/test_integration.py -v --tb=short

# Clean up everything
clean: down
	docker rmi runtime-api-runtime-api 2>/dev/null || true
