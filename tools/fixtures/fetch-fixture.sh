#!/bin/bash
# fetch-fixture.sh <fixture-name> — fetch a capture.v1 golden to the local cache.
# Resolves contracts/capture/v1/goldens/<name>.json, syncs from S3, verifies sha256.
# Env: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (staging S3 read creds).
set -euo pipefail
NAME="$1"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PTR="$REPO_ROOT/contracts/capture/v1/goldens/$NAME.json"
[ -f "$PTR" ] || { echo "unknown fixture: $NAME (no golden pointer)"; exit 1; }
CACHE="${VEXA_FIXTURE_CACHE:-$HOME/.vexa/fixtures}/$NAME"
S3=$(python3 -c "import json;print(json.load(open('$PTR'))['s3'])")
EP=$(python3 -c "import json;print(json.load(open('$PTR'))['endpoint'])")
mkdir -p "$CACHE"
aws s3 sync "$S3" "$CACHE/" --endpoint-url "$EP" --quiet
python3 - "$PTR" "$CACHE" <<'PY'
import json,sys,hashlib,os
ptr,cache=json.load(open(sys.argv[1])),sys.argv[2]
for rel,want in ptr['files'].items():
    got=hashlib.sha256(open(os.path.join(cache,rel),'rb').read()).hexdigest()
    assert got==want, f"sha256 mismatch: {rel}"
print(f"✅ {ptr['name']} verified ({len(ptr['files'])} files) → {cache}")
PY
