#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Argument Parsing & Configuration
if [ -z "$1" ]; then
    echo ">> Mode: Standard Build (All Targets)"
    # Standard: No special flags, builds everything defined in 'else' block
    CMAKE_ARGS=""
    BUILD_TARGET="all"
elif [ "$1" == "-dev" ]; then
    echo ">> Mode: Standalone Build (RF Only)"
    # Dev: Sets the flag to trigger the 'if(BUILD_STANDALONE)' block
    CMAKE_ARGS="-DBUILD_STANDALONE=ON"
    BUILD_TARGET="rf_app"
else
    echo "Error: Invalid parameter."
    echo "Usage: ./build.sh [-dev]"
    exit 1
fi

# 2. Setup Build Directory
# Clean slate
rm -rf build
mkdir build
cd build

# 3. Configure CMake
echo ">> Configuring CMake..."
cmake $CMAKE_ARGS .. > /dev/null

# 4. Compile
echo ">> Compiling..."
cmake --build . --target $BUILD_TARGET

# 5. Move Binaries & Cleanup
echo ">> Moving binaries to root and cleaning up..."
cd ..

# Move the resulting files based on the mode
if [ "$1" == "-dev" ]; then
    # Standalone mode only creates rf_app
    [ -f build/rf_app ] && mv build/rf_app .
    echo ">> artifacts: ./rf_app (Standalone)"
else
    # Standard mode creates both
    [ -f build/rf_app ] && mv build/rf_app .
    [ -f build/ltegps_app ] && mv build/ltegps_app .
    echo ">> artifacts: ./rf_app, ./ltegps_app (Standard)"
fi

# 6. Erase Build Folder
rm -rf build

echo ">> Done."