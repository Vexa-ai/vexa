#!/usr/bin/env bash
# Stub for Phase 1 (PREFL-04): functional test entry for the snapshots feature.
# Will be replaced with real assertions in Phase 5 (SHIP-03) after Phases 2-4
# ship the worker, endpoint, and gallery. Reference: features/snapshots/README.md
source "$(dirname "$0")/../lib/common.sh"

echo ""
echo "  snapshots"
echo "  ──────────────────────────────────────────────"

test_begin "snapshots"

step_skip extraction_pending_implementation "Phase 2 will implement frame extraction"
step_skip endpoint_pending_implementation "Phase 3 will add the endpoint"
step_skip gallery_pending_implementation "Phase 4 will add the gallery"

echo "  ──────────────────────────────────────────────"
echo ""

test_end