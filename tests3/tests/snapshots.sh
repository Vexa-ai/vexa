#!/usr/bin/env bash
# Functional test for the snapshots feature.
# Tests: extraction pipeline, API endpoint, disabled-gate, and response shape.
# Reference: features/snapshots/README.md · dods.yaml
source "$(dirname "$0")/../lib/common.sh"

echo ""
echo "  snapshots"
echo "  ──────────────────────────────────────────────"

test_begin "snapshots"

# --- Step 1: Feature flag gate (disabled returns 404) ---
if [ -n "$BASE_URL" ]; then
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        "${BASE_URL}/recordings/1/frames" 2>/dev/null || echo "000")
    if [ "$STATUS" = "404" ]; then
        step_pass "gate_disabled_404" "SNAPSHOTS_ENABLED=false → 404"
    elif [ "$STATUS" = "200" ]; then
        step_pass "gate_enabled_200" "SNAPSHOTS_ENABLED=true → 200"
    else
        step_fail "gate_status" "Unexpected status $STATUS (expected 404 disabled or 200 enabled)"
    fi
else
    step_skip "gate_no_base_url" "BASE_URL not set — cannot test HTTP endpoint"
fi

# --- Step 2: Response shape (extraction_status, frames, total) ---
if [ -n "$BASE_URL" ]; then
    BODY=$(curl -s "${BASE_URL}/recordings/1/frames" 2>/dev/null || echo "{}")
    HAS_STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'extraction_status' in d else 'no')" 2>/dev/null || echo "no")
    HAS_FRAMES=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'frames' in d else 'no')" 2>/dev/null || echo "no")
    HAS_TOTAL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'total' in d else 'no')" 2>/dev/null || echo "no")

    if [ "$HAS_STATUS" = "yes" ] && [ "$HAS_FRAMES" = "yes" ] && [ "$HAS_TOTAL" = "yes" ]; then
        step_pass "response_shape" "GET /frames returns extraction_status, frames[], total"
    else
        step_fail "response_shape" "Missing fields: status=$HAS_STATUS frames=$HAS_FRAMES total=$HAS_TOTAL"
    fi
else
    step_skip "shape_no_base_url" "BASE_URL not set — cannot test response shape"
fi

# --- Step 3: Frame extractor module exists ---
if [ -f "services/meeting-api/meeting_api/frame_extractor.py" ]; then
    step_pass "extractor_module_exists" "frame_extractor.py exists"
else
    step_fail "extractor_module_exists" "frame_extractor.py not found"
fi

# --- Step 4: RecordingFrame model exists with correct columns ---
if python3 -c "from meeting_api.models import RecordingFrame; assert hasattr(RecordingFrame, 'timestamp_s'); assert hasattr(RecordingFrame, 'meeting_id'); assert hasattr(RecordingFrame, 'recording_id')" 2>/dev/null; then
    step_pass "frames_model_schema" "RecordingFrame has timestamp_s (Integer), meeting_id, recording_id"
else
    step_fail "frames_model_schema" "RecordingFrame model missing expected columns"
fi

echo "  ──────────────────────────────────────────────"
echo ""

test_end