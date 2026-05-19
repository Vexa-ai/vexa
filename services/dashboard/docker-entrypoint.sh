#!/bin/sh
# Patch Next.js standalone rewrites with runtime VEXA_API_URL.
# Rewrites in next.config.ts are baked at build time. This script
# replaces the build-time placeholder with the runtime value before
# starting the server.

if [ -n "$VEXA_API_URL" ]; then
  # Next.js standalone bakes rewrite destinations at build time into multiple files.
  # Replace the build-time default (http://localhost:8066) with the runtime VEXA_API_URL.
  for f in .next/required-server-files.json .next/routes-manifest.json; do
    if [ -f "$f" ]; then
      sed -i "s|http://localhost:8066|${VEXA_API_URL}|g" "$f"
    fi
  done
  echo "Patched rewrites: localhost:8066 -> ${VEXA_API_URL}"
fi

exec node server.js
