#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sh -n bunny/boot.sh
sh -n bunny/cron-job.sh
bash -n bunny/build-push.sh
grep -q 'JACRED_CONFIG_DIR=/app/config' Dockerfile.bunny
grep -q 'bunny/boot.sh' Dockerfile.bunny
grep -q 'ResolveConfigPath' Configuration/AppConfigurationLoader.cs
grep -q 'JACRED_BUNNY_API_KEY' bunny/boot.sh
grep -q 'api/v2.0/indexers/all/results' bunny/README.md
echo 'BUNNY_SOURCE_SMOKE_PASS (not a dotnet build or live deployment test)'
