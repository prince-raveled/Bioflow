#!/usr/bin/env bash
# Build BioFlow's analysis image with whichever OCI runtime is present.
#
# Podman is preferred: it is what Fedora and RHEL ship, it needs no daemon, and
# rootless it needs no group whose membership is equivalent to root. Docker
# works identically - the image is OCI, not Docker-specific.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${BIOFLOW_IMAGE:-localhost/bioflow-tools}"
TAG="${BIOFLOW_IMAGE_TAG:-0.1.0}"

if command -v podman >/dev/null 2>&1; then
    RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then
    RUNTIME=docker
else
    echo "No container runtime found. Install podman (preferred) or docker." >&2
    exit 1
fi

echo "Building ${IMAGE}:${TAG} with ${RUNTIME}"
"$RUNTIME" build --tag "${IMAGE}:${TAG}" --file "${HERE}/Dockerfile" "${HERE}"

echo
echo "Built ${IMAGE}:${TAG}"
"$RUNTIME" image inspect "${IMAGE}:${TAG}" \
    --format '  digest: {{.Digest}}{{"\n"}}  size:   {{.Size}} bytes'
echo
echo "Pin runs to the digest, not the tag: a tag can be moved, a digest cannot."
