.PHONY: build up down test test-unit test-integration health

build:
	docker compose build

up: build
	docker compose up -d
	@echo "Waiting for meeting-api..."
	@timeout 60 sh -c 'until curl -sf http://localhost:8080/health > /dev/null 2>&1; do sleep 2; done' \
		&& echo "meeting-api is up: http://localhost:8080" \
		|| (echo "ERROR: meeting-api did not start"; docker compose logs meeting-api; exit 1)

down:
	docker compose down -v

test: test-unit test-integration

test-unit:
	pytest tests/ -v --ignore=tests/test_integration_live.py --ignore=tests/collector/

test-integration: up
	MEETING_API_URL=http://localhost:8080 pytest tests/test_integration_live.py -v --tb=short

health:
	@curl -sf http://localhost:8080/health | python3 -m json.tool
