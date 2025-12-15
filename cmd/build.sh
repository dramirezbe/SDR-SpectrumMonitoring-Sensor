#!/usr/bin/env bash
# cmd/build.sh - build a single Python module from root_dir/app using PyInstaller
#
# Purpose:
#   Build a single Python module located in <project_root>/app into a flat
#   executable placed in <project_root>/build/<module_name>.  Temporary build
#   files are placed in <project_root>/build_tmp and removed when the script
#   finishes (including on error).
#
# Usage:
#   ./cmd/build.sh <module_name>
# Example:
#   ./cmd/build.sh init_system
#
# Behavior (unchanged):
#   - Uses python -m PyInstaller --onefile
#   - Uses --distpath set to PROJECT_ROOT/build (flat executables)
#   - Uses BUILD_TMP for PyInstaller work/spec files and removes it on exit
#   - Validates that app/<module_name>.py exists and will exit non-zero if not
#
set -euo pipefail

# --- basic paths ---
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")
APP_DIR="$PROJECT_ROOT/app"
BUILD_DIR="$PROJECT_ROOT/build"
BUILD_TMP="$PROJECT_ROOT/build_tmp"

# --- args ---
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <module_name>" >&2
    exit 1
fi

MODULE_NAME="$1"
MODULE_FILENAME="${MODULE_NAME}.py"
MODULE_PATH="$APP_DIR/$MODULE_FILENAME"

# --- quick validations ---
if ! command -v python >/dev/null 2>&1; then
    echo "ERROR: No 'python' found in PATH. Activate your venv or install Python." >&2
    exit 10
fi

if ! python -c "import PyInstaller" >/dev/null 2>&1; then
    echo "ERROR: PyInstaller python package not available in the active Python environment." >&2
    echo "       Install it with: python -m pip install pyinstaller" >&2
    exit 11
fi

if [ ! -f "$MODULE_PATH" ]; then
    echo "ERROR: Source file not found: $MODULE_PATH" >&2
    exit 2
fi

# --- prepare directories ---

mkdir -p "$BUILD_DIR"
rm -rf "$BUILD_TMP"
mkdir -p "$BUILD_TMP"

# --- cleanup & final-status trap ---
OLD_PWD="$(pwd)"
_exit_status=0
cleanup() {
    local rc="${_exit_status:-$?}"
    # return to original cwd (best-effort)
    cd "$OLD_PWD" || true
    # remove temporary build dir
    rm -rf "$BUILD_TMP" >/dev/null 2>&1 || true

    if [ "$rc" -eq 0 ]; then
        printf '%s\n' "---------------- BUILD SUCCEEDED ----------------"
        printf '%s\n' "Executable: $BUILD_DIR/$MODULE_NAME"
        printf '%s\n' "-------------------------------------------------"
    else
        printf '%s\n' "----------------- BUILD FAILED ------------------"
        printf '%s\n' "Exit code: $rc"
        printf '%s\n' "Temporary build files (if any) removed from: $BUILD_TMP"
        printf '%s\n' "-------------------------------------------------"
    fi
}
trap ' _exit_status=$?; cleanup; exit $_exit_status' EXIT

printf '%s\n\n' "----------- building $MODULE_FILENAME -----------"

cd "$APP_DIR"

printf '%s\n' 'Invoking PyInstaller...'
printf '%s\n\n' "  python -m PyInstaller --onefile --name \"$MODULE_NAME\" --workpath \"build_tmp\" --specpath \"build_tmp\" --distpath \"build\" --paths \".\" --noconfirm --log-level=WARN \"$MODULE_FILENAME\""

# Run PyInstaller
python -m PyInstaller \
  --onefile \
  --name "$MODULE_NAME" \
  --workpath "$BUILD_TMP" \
  --specpath "$BUILD_TMP" \
  --distpath "$BUILD_DIR" \
  --paths "." \
  --noconfirm \
  --log-level=WARN \
  "$MODULE_FILENAME"