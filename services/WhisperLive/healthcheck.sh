#!/bin/sh
set -e
python -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://localhost:9091/health', timeout=3)
except Exception:
    sys.exit(1)
"
