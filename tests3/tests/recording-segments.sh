#!/usr/bin/env bash
# Test incremental recording segment upload and WAV concatenation.
# Reads: .state/gateway_url, .state/api_token
# Requires: running meeting-api with MinIO
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url 2>/dev/null || echo "http://localhost:8056")
API_TOKEN=$(state_read api_token 2>/dev/null || echo "")
MEETING_API_URL=$(state_read meeting_api_url 2>/dev/null || echo "http://localhost:8080")

echo ""
echo "  recording-segments"
echo "  ──────────────────────────────────────────────"

# Use internal endpoint directly (no auth required for /internal/)
UPLOAD_URL="$MEETING_API_URL/internal/recordings/upload"

# ── 0. Generate test WAV segments ────────────────
# Create 3 small WAV segments with known PCM data (silence + marker bytes)

generate_wav_segment() {
    local outfile="$1"
    local duration_samples="$2"  # number of 16-bit mono samples
    local marker="$3"            # byte value to fill PCM data with

    local data_size=$((duration_samples * 2))  # 16-bit = 2 bytes/sample
    local file_size=$((36 + data_size))
    local sample_rate=16000
    local byte_rate=32000
    local block_align=2

    python3 -c "
import struct, sys
data_size = $data_size
header = bytearray(44)
header[0:4] = b'RIFF'
struct.pack_into('<I', header, 4, 36 + data_size)
header[8:12] = b'WAVE'
header[12:16] = b'fmt '
struct.pack_into('<I', header, 16, 16)
struct.pack_into('<H', header, 20, 1)  # PCM
struct.pack_into('<H', header, 22, 1)  # mono
struct.pack_into('<I', header, 24, $sample_rate)
struct.pack_into('<I', header, 28, $byte_rate)
struct.pack_into('<H', header, 32, $block_align)
struct.pack_into('<H', header, 34, 16)  # bits per sample
header[36:40] = b'data'
struct.pack_into('<I', header, 40, data_size)
sys.stdout.buffer.write(bytes(header))
sys.stdout.buffer.write(bytes([$marker] * data_size))
" > "$outfile"
}

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

SESSION_UID="test-seg-$(date +%s)"

# 3 segments: 1600 samples each = 0.1s at 16kHz
generate_wav_segment "$TMPDIR/seg0.wav" 1600 0x10
generate_wav_segment "$TMPDIR/seg1.wav" 1600 0x20
generate_wav_segment "$TMPDIR/seg2.wav" 1600 0x30

pass "generated 3 test WAV segments"

# ── 1. Test WAV concatenation logic (pure Python) ─
echo "  testing WAV concatenation logic..."

DB_HOST=x DB_PORT=1 DB_NAME=x DB_USER=x DB_PASSWORD=x REDIS_URL=redis://x:6379 python3 -c "
import struct, sys, os
sys.path.insert(0, '$ROOT/services/meeting-api')

# Test the concatenation function with in-memory mock storage
class MockStorage:
    def __init__(self):
        self.files = {}
    def upload_file(self, path, data, content_type=None):
        self.files[path] = data
    def download_file(self, path):
        return self.files[path]
    def list_files(self, prefix):
        return sorted(k for k in self.files if k.startswith(prefix))
    def delete_file(self, path):
        del self.files[path]

# Read test segments
with open('$TMPDIR/seg0.wav', 'rb') as f: seg0 = f.read()
with open('$TMPDIR/seg1.wav', 'rb') as f: seg1 = f.read()
with open('$TMPDIR/seg2.wav', 'rb') as f: seg2 = f.read()

# Upload segments to mock storage
storage = MockStorage()
storage.upload_file('recordings/1/100/segments/${SESSION_UID}_seg_0000.wav', seg0)
storage.upload_file('recordings/1/100/segments/${SESSION_UID}_seg_0001.wav', seg1)

# Import and test concatenation
from meeting_api.recordings import _concatenate_wav_segments, _cleanup_segments, _segment_storage_path

# Test segment path generation
path = _segment_storage_path(1, 100, '${SESSION_UID}', 5, 'wav')
assert 'seg_0005' in path, f'Expected seg_0005 in path, got {path}'

# Test concatenation (2 segments in storage + 1 final segment)
data, duration = _concatenate_wav_segments(storage, 1, 100, '${SESSION_UID}', 'wav', seg2)

# Verify output is valid WAV
assert data[:4] == b'RIFF', 'Missing RIFF header'
assert data[8:12] == b'WAVE', 'Missing WAVE marker'
assert data[36:40] == b'data', 'Missing data marker'

# Verify total PCM size = 3 segments * 1600 samples * 2 bytes = 9600 bytes
total_data_size = struct.unpack_from('<I', data, 40)[0]
assert total_data_size == 9600, f'Expected 9600 data bytes, got {total_data_size}'

# Verify RIFF size = 36 + 9600 = 9636
riff_size = struct.unpack_from('<I', data, 4)[0]
assert riff_size == 9636, f'Expected RIFF size 9636, got {riff_size}'

# Verify total file size = 44 + 9600 = 9644
assert len(data) == 9644, f'Expected 9644 total bytes, got {len(data)}'

# Verify PCM data markers are correct (0x10, 0x20, 0x30 from each segment)
pcm = data[44:]
assert pcm[0] == 0x10, f'Segment 0 marker wrong: {pcm[0]:#x}'
assert pcm[3200] == 0x20, f'Segment 1 marker wrong: {pcm[3200]:#x}'
assert pcm[6400] == 0x30, f'Segment 2 marker wrong: {pcm[6400]:#x}'

# Verify duration = 9600 / (16000 * 1 * 2) = 0.3s
assert abs(duration - 0.3) < 0.01, f'Expected ~0.3s duration, got {duration}'

# Test cleanup
deleted = _cleanup_segments(storage, 1, 100, '${SESSION_UID}')
assert deleted == 2, f'Expected 2 deleted, got {deleted}'
assert len(storage.files) == 0, f'Expected empty storage, got {len(storage.files)} files'

print('ALL ASSERTIONS PASSED')
" 2>&1

if [ $? -eq 0 ]; then
    pass "WAV concatenation logic correct"
else
    fail "WAV concatenation logic failed"
    exit 1
fi

# ── 2. Test segment storage path format ──────────
echo "  testing segment path format..."

DB_HOST=x DB_PORT=1 DB_NAME=x DB_USER=x DB_PASSWORD=x REDIS_URL=redis://x:6379 python3 -c "
import sys
sys.path.insert(0, '$ROOT/services/meeting-api')
from meeting_api.recordings import _segment_storage_path

p = _segment_storage_path(42, 999, 'abc-123', 0, 'wav')
assert p == 'recordings/42/999/segments/abc-123_seg_0000.wav', f'Bad path: {p}'

p = _segment_storage_path(42, 999, 'abc-123', 15, 'wav')
assert p == 'recordings/42/999/segments/abc-123_seg_0015.wav', f'Bad path: {p}'

print('ALL ASSERTIONS PASSED')
" 2>&1

if [ $? -eq 0 ]; then
    pass "segment path format correct"
else
    fail "segment path format failed"
    exit 1
fi

echo ""
echo "  ──────────────────────────────────────────────"
pass "recording-segments: all tests passed"
