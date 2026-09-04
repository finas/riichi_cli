#!/usr/bin/env bash
set -euo pipefail

model_dir="${MORTAL_MODEL_DIR:-${HOME}/Models/mortal}"

if [[ ! -f "${model_dir}/mortal.pth" ]]; then
    echo "Mortal model not found: ${model_dir}/mortal.pth" >&2
    exit 1
fi

exec docker run --platform linux/amd64 -i --rm \
    -v "${model_dir}:/mnt:ro" \
    mortal:latest "$@"
