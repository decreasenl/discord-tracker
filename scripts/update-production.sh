#!/bin/sh
set -eu

# Resolve the checkout relative to this script, regardless of the caller's cwd.
cd "$(dirname "$0")/.."

exec docker compose -p discord-tracker-prod up -d --build --force-recreate
