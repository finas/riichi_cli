#!/bin/sh
# Launch bundled Akochan in MJAI pipe mode. The caller appends seat (0..3).
set -eu
ENGINE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DYLD_LIBRARY_PATH="$ENGINE:/opt/homebrew/opt/llvm/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
cd "$ENGINE"
exec "$ENGINE/akochan" pipe "$ENGINE/setup_mjai.json" "$1"
