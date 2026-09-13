#!/usr/bin/env bash
# Build against an existing toolchain usd-dev; never resolves or builds inputs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${BUILD_DIR:-$HERE/build}"
PREFIX="${PREFIX:-$HERE/out}"
: "${USD_DEV:?Set USD_DEV to the toolchain usd-dev output containing pxrConfig.cmake}"
test -f "$USD_DEV/pxrConfig.cmake"
PREFIX_PATH="$USD_DEV"
if [ -f "$USD_DEV/nix-support/propagated-build-inputs" ]; then
  for dep in $(cat "$USD_DEV/nix-support/propagated-build-inputs"); do
    PREFIX_PATH="$PREFIX_PATH;$dep"
  done
fi
USD_PYTHON_ROOT="$(sed -n 's/.*set(Python3_EXECUTABLE \[\[\(.*\)\/bin\/python3.*\]\]).*/\1/p' "$USD_DEV/pxrConfig.cmake" | head -1)"
echo '== stage: configure usdIfc'
cmake -S "$HERE" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release -Dpxr_DIR="$USD_DEV" \
  -DCMAKE_PREFIX_PATH="$PREFIX_PATH" -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  ${USD_PYTHON_ROOT:+-DPython3_ROOT_DIR="$USD_PYTHON_ROOT" -DPython3_FIND_STRATEGY=LOCATION} "$@"
echo '== stage: build usdIfc'
cmake --build "$BUILD_DIR" --parallel "${JOBS:-4}"
echo '== stage: install usdIfc'
cmake --install "$BUILD_DIR"
