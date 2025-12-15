#!/usr/bin/env bash
# cmd/build-all.sh - build the canonical set of modules (uses cmd/build.sh)
# No args. Runs each module with cmd/build.sh, catches errors and continues.
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")
BUILD_SCRIPT="$SCRIPT_DIR/build.sh"

MODULES=(
    orchestrator
    demod_consumer
    campaign_runner
    kal_sync
    ntp_sync
    psd_consumer
    realtime_runner
    status_device
    init_system
    retry_queue
)

# sanity: ensure build script exists
if [ ! -x "$BUILD_SCRIPT" ]; then
    echo "ERROR: build script not found or not executable: $BUILD_SCRIPT" >&2
    exit 2
fi

echo
printf '%s\n' "================ BUILD-ALL START ================"
printf '%s\n' "Project root: $PROJECT_ROOT"
printf '%s\n' "Using build script: $BUILD_SCRIPT"
printf '%s\n' "Modules: ${MODULES[*]}"
printf '%s\n\n' "-----------------------------------------------"

SUCCESS=()
FAIL=()

for mod in "${MODULES[@]}"; do
    if "$BUILD_SCRIPT" "$mod"; then
        printf '%s\n' ">>> build succeeded: $mod"
        SUCCESS+=("$mod")
    else
        rc=$?
        printf '%s\n' ">>> build FAILED: $mod (exit $rc)"
        FAIL+=("$mod")
    fi
done

# summary
printf '\n%s\n' "================ BUILD-ALL SUMMARY ================"
printf '%s\n' "Succeeded: ${#SUCCESS[@]}"
if [ "${#SUCCESS[@]}" -gt 0 ]; then
    printf '  %s\n' "${SUCCESS[@]}"
fi
printf '%s\n' "Failed:    ${#FAIL[@]}"
if [ "${#FAIL[@]}" -gt 0 ]; then
    printf '  %s\n' "${FAIL[@]}"
fi
printf '%s\n' "=================================================="

# exit non-zero if any failed
if [ "${#FAIL[@]}" -gt 0 ]; then
    exit 1
fi
exit 0