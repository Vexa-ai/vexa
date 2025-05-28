#!/bin/sh
set -e

# Use fixed health check port for both GPU and CPU versions
HEALTH_PORT=9091

# Using curl to check health endpoint
curl -sf "http://localhost:${HEALTH_PORT}/health" > /dev/null 