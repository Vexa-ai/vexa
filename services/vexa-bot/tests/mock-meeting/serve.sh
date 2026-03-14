#!/bin/bash
# Serve mock meeting page on port 8080
cd "$(dirname "$0")"
echo "Mock meeting at http://localhost:8080"
echo "3 participants: Alice (JennyNeural), Bob (GuyNeural), Carol (SoniaNeural)"
python3 -m http.server 8080
