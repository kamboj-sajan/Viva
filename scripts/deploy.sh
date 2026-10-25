#!/usr/bin/env bash
# Build the image for linux/amd64, push it to the Azure registry and roll the container app to it.
# (Azure for Students doesn't allow registry-side builds, so the image is built locally.)
set -euo pipefail
ACR=${ACR:-vivaacrcnq7w}
APP=${APP:-viva}
RG=${RG:-rg-viva}
TAG=${1:-$(git rev-parse --short HEAD)}

az acr login -n "$ACR"
docker buildx build --platform linux/amd64 -t "$ACR.azurecr.io/viva:$TAG" --push .
az containerapp update -g "$RG" -n "$APP" --image "$ACR.azurecr.io/viva:$TAG" -o none
echo "Deployed $TAG: https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
