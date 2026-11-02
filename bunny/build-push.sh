#!/usr/bin/env bash
set -euo pipefail
IMAGE="$(printenv IMAGE || true)"
if [ -z "$IMAGE" ]; then
  echo "Usage: IMAGE=ghcr.io/YOUR_ACCOUNT/jacred-bunny:VERSION bash bunny/build-push.sh" >&2
  exit 2
fi
command -v docker >/dev/null || { echo "Docker Buildx required" >&2; exit 2; }
SHA="$(git rev-parse HEAD)"
echo "Building this fork: $SHA"
docker buildx build --platform linux/amd64 \
  --provenance=false --sbom=false \
  --build-arg "JACRED_VERSION=$SHA" \
  -f Dockerfile.bunny -t "$IMAGE" --push .
echo "IMAGE_PUBLISHED=$IMAGE"
echo "Mount persistent /app/config and /app/Data; port 9117; 1 replica."
