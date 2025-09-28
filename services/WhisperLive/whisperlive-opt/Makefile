# WhisperLive Optimization Testing Makefile
# Provides convenient targets for running optimization tests

.PHONY: help install clean setup-data run-baseline run-greedy run-beam run-sweep fetch-samples prep-audio

# Default configuration
CONFIG ?= configs/baseline.yaml
OUTPUT ?= results/$(shell date +%Y%m%d_%H%M%S)
PYTHON ?= python3
PIP ?= pip3

# Help target
help: ## Show this help message
	@echo "WhisperLive Optimization Testing"
	@echo "================================"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make run-baseline                    # Run baseline test"
	@echo "  make run-greedy                      # Run greedy decoding test"
	@echo "  make run-sweep CONFIG=configs/sweep_num_workers.yaml  # Run parameter sweep"
	@echo "  make fetch-samples                   # Download YouTube samples"
	@echo ""

# Installation and setup
install: ## Install Python dependencies
	$(PIP) install -r requirements.txt

install-dev: ## Install development dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install pytest black flake8 mypy

# Data preparation
setup-data: ## Create data directory structure
	mkdir -p data/audio data/goldens
	@echo "Data directories created"

fetch-samples: ## Download YouTube audio samples (requires WhisperLive server running)
	@echo "Fetching YouTube audio samples..."
	@echo "Make sure WhisperLive server is running on ws://localhost:9090/ws"
	@echo "Example URLs:"
	@echo "  python scripts/fetch_youtube_samples.py --urls 'https://www.youtube.com/watch?v=example1,https://www.youtube.com/watch?v=example2'"
	@echo ""

prep-audio: ## Prepare audio samples (resample, normalize)
	$(PYTHON) scripts/prepare_audio.py --in data/audio --out data/audio

# Test execution
run-e2e: ## Run end-to-end test with specified config
	@echo "Running optimization test with config: $(CONFIG)"
	@echo "Output directory: $(OUTPUT)"
	$(PYTHON) -m harness.runner --config $(CONFIG) --out $(OUTPUT)

run-baseline: ## Run baseline configuration test
	@$(MAKE) run-e2e CONFIG=configs/baseline.yaml OUTPUT=results/baseline_$(shell date +%Y%m%d_%H%M%S)

run-greedy: ## Run greedy decoding configuration test
	@$(MAKE) run-e2e CONFIG=configs/greedy.yaml OUTPUT=results/greedy_$(shell date +%Y%m%d_%H%M%S)

run-beam: ## Run beam search configuration test
	@$(MAKE) run-e2e CONFIG=configs/beam_search.yaml OUTPUT=results/beam_$(shell date +%Y%m%d_%H%M%S)

run-sweep: ## Run parameter sweep test
	@$(MAKE) run-e2e CONFIG=configs/sweep_num_workers.yaml OUTPUT=results/sweep_$(shell date +%Y%m%d_%H%M%S)

# Quality assessment
run-with-llm: ## Run test with LLM judge enabled
	$(PYTHON) -m harness.runner --config $(CONFIG) --out $(OUTPUT) --llm-judge

run-quality-only: ## Run quality assessment on existing results
	@echo "Quality assessment only - requires existing transcripts in $(OUTPUT)"
	$(PYTHON) -m harness.runner --config $(CONFIG) --out $(OUTPUT) --no-simple-quality

# Development and testing
test: ## Run unit tests
	$(PYTHON) -m pytest tests/ -v

lint: ## Run code linting
	flake8 harness/ scripts/ --max-line-length=100 --ignore=E203,W503
	black --check harness/ scripts/

format: ## Format code with black
	black harness/ scripts/

type-check: ## Run type checking with mypy
	mypy harness/ --ignore-missing-imports

# Server management
start-server: ## Start WhisperLive server (example command)
	@echo "Starting WhisperLive server..."
	@echo "Example command:"
	@echo "  cd .. && python run_server.py --port 9090 --backend faster_whisper"
	@echo ""

stop-server: ## Stop WhisperLive server (example command)
	@echo "Stopping WhisperLive server..."
	@echo "Use Ctrl+C or kill the process"

# Results and analysis
show-results: ## Show latest test results
	@echo "Latest results:"
	@ls -la results/ | tail -10

analyze-results: ## Analyze results with pandas (requires results)
	$(PYTHON) -c "import pandas as pd; df = pd.read_csv('$(OUTPUT)/per_second.csv'); print(df.describe())"

plot-results: ## Generate performance plots (requires matplotlib)
	$(PYTHON) scripts/plot_results.py --input $(OUTPUT)

# Cleanup
clean: ## Clean up temporary files and cache
	rm -rf __pycache__/
	rm -rf *.pyc
	rm -rf .pytest_cache/
	rm -rf cache/
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete

clean-results: ## Clean up test results
	rm -rf results/*

clean-all: clean clean-results ## Clean everything

# Docker support
docker-build: ## Build Docker image for optimization harness
	docker build -t whisperlive-opt .

docker-build-server: ## Build WhisperLive server Docker image
	cd .. && docker build -f docker/Dockerfile.gpu -t whisperlive-server .

docker-up-server: ## Start WhisperLive server container
	docker-compose up -d whisperlive-server

docker-down-server: ## Stop WhisperLive server container
	docker-compose down whisperlive-server

docker-logs-server: ## View WhisperLive server logs
	docker-compose logs -f whisperlive-server

docker-up-optimizer: ## Start optimization harness container
	docker-compose --profile optimizer up -d whisperlive-optimizer

docker-run-test: ## Run optimization test in Docker container
	docker-compose --profile optimizer run --rm whisperlive-optimizer \
		python -m harness.runner --config $(CONFIG) --out /app/results

docker-run-baseline: ## Run baseline test in Docker
	@$(MAKE) docker-run-test CONFIG=configs/baseline.yaml

docker-run-sweep: ## Run parameter sweep in Docker
	@$(MAKE) docker-run-test CONFIG=configs/sweep_num_workers.yaml

docker-shell: ## Get shell access to optimization container
	docker-compose --profile optimizer run --rm whisperlive-optimizer bash

docker-dev: ## Start development environment (server + optimizer)
	docker-compose --profile dev up -d
	@echo "Server running at ws://localhost:9090/ws"
	@echo "Run: docker-compose --profile optimizer exec whisperlive-optimizer bash"

docker-clean: ## Clean up Docker containers and volumes
	docker-compose down -v
	docker system prune -f

# Configuration validation
validate-config: ## Validate YAML configuration
	$(PYTHON) -m harness.runner --config $(CONFIG) --dry-run

# Quick test (short duration)
quick-test: ## Run quick test (short duration)
	$(PYTHON) -m harness.runner --config configs/baseline.yaml --out results/quick_$(shell date +%Y%m%d_%H%M%S)

# Stress test (high concurrency)
stress-test: ## Run stress test (high concurrency)
	@echo "Creating stress test config..."
	@sed 's/concurrency: 32/concurrency: 64/g; s/run_s: 120/run_s: 60/g' configs/baseline.yaml > configs/stress.yaml
	@$(MAKE) run-e2e CONFIG=configs/stress.yaml OUTPUT=results/stress_$(shell date +%Y%m%d_%H%M%S)
	@rm configs/stress.yaml

# Benchmark comparison
benchmark: ## Run benchmark comparison (baseline vs greedy vs beam)
	@echo "Running benchmark comparison..."
	@$(MAKE) run-baseline
	@$(MAKE) run-greedy  
	@$(MAKE) run-beam
	@echo "Benchmark completed. Check results/ directory."

# Documentation
docs: ## Generate documentation
	@echo "Generating documentation..."
	@echo "See README.md for usage instructions"

# Environment setup
setup-env: ## Set up development environment
	@echo "Setting up development environment..."
	@echo "1. Install dependencies: make install-dev"
	@echo "2. Set up data: make setup-data"
	@echo "3. Start WhisperLive server: make start-server"
	@echo "4. Fetch samples: make fetch-samples"
	@echo "5. Run test: make run-baseline"

# Status check
status: ## Check system status
	@echo "WhisperLive Optimization Status"
	@echo "==============================="
	@echo "Python: $(shell $(PYTHON) --version)"
	@echo "Pip: $(shell $(PIP) --version)"
	@echo "Current directory: $(PWD)"
	@echo "Config file: $(CONFIG)"
	@echo "Output directory: $(OUTPUT)"
	@echo ""
	@echo "Dependencies:"
	@$(PYTHON) -c "import sys; print('✓ Python', sys.version.split()[0])" 2>/dev/null || echo "✗ Python not found"
	@$(PYTHON) -c "import asyncio, websockets, numpy, pandas, yaml, rich" 2>/dev/null && echo "✓ Core dependencies" || echo "✗ Missing dependencies"
	@$(PYTHON) -c "import librosa, soundfile" 2>/dev/null && echo "✓ Audio dependencies" || echo "✗ Missing audio dependencies"
	@$(PYTHON) -c "import yt_dlp" 2>/dev/null && echo "✓ YouTube dependencies" || echo "✗ Missing YouTube dependencies"
	@echo ""
	@echo "Data directory:"
	@ls -la data/ 2>/dev/null || echo "✗ Data directory not found"
	@echo ""
	@echo "Results directory:"
	@ls -la results/ 2>/dev/null || echo "✗ Results directory not found"
