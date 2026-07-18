# core/meetings/eval — autonomous eval legs.
#
#   make counting-ci     the L3.5 wav→words value leg (#560): committed counting audio → local
#                        CPU whisper (deploy/transcription/docker-compose.cpu.yml) → the real
#                        collector → API-served words, 1..N oracle with per-stage attribution
#                        (+ the STT-down negative control). Brings the compose stack AND the CPU
#                        whisper up itself; needs docker only.
#
# Knobs: COUNTING_STT_MODEL (default tiny — fits the CI budget; small = the live-meeting default),
#        IMAGE_TAG / COMPOSE_NO_BUILD (pin published images instead of building the tree),
#        COMPOSE_DYNAMIC_PORTS=1 (run beside a developer stack).
REPO := $(abspath $(CURDIR)/../../..)

.PHONY: counting-ci
counting-ci:
	cd $(REPO)/deploy/compose && COUNTING_STT=1 ./bin/stack-test counting_stt_test.py
