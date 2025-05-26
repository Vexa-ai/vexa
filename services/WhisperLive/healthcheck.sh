#!/bin/sh
set -e

HEALTH_PORT=$1

if [ -z "$HEALTH_PORT" ]; then
  # This output will go to Docker's health check log, visible in 'docker inspect'
  echo "Error: Health check port not provided to healthcheck.sh" >&2
  exit 1
fi

# Using full path to curl
/usr/bin/curl -s -f "http://localhost:${HEALTH_PORT}/health" > /dev/null 