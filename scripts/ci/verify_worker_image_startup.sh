#!/usr/bin/env bash

set -euo pipefail

image_tag="ah-05-04-worker-startup-test:latest"

cleanup() {
  docker image rm --force "${image_tag}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --file ai_worker/Dockerfile --tag "${image_tag}" .
docker run --rm \
  --env PROTECTED_RETRIEVAL_ENABLED=false \
  "${image_tag}" \
  uv run --no-sync python -c \
  "from ai_worker.core.config import Config; assert Config.model_fields['PROTECTED_RETRIEVAL_ENABLED'].default is False; import ai_worker.main; import ai_worker.adapters.postgresql_protected_retrieval_control; import ai_worker.adapters.sqlalchemy_source_snapshot_repository; import ai_worker.admin.catalog_writer; print('worker-image-protected-off-import-ok')"
