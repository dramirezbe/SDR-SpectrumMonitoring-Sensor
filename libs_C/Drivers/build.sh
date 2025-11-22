#!/bin/bash

# Configuration
BUILD_DIR="build"
TARGET_NAME="main"

echo "### Starting CMake Build Process (Clean Build) ###"
echo "---"

# 1. Start with a clean build directory
if [ -d "$BUILD_DIR" ]; then
    echo "Removing existing build directory: $BUILD_DIR"
    rm -rf "$BUILD_DIR"
fi
mkdir -p "$BUILD_DIR"
echo "Created new build directory: $BUILD_DIR"
echo "---"

# 2. Configure the project using CMake
echo "Configuring project with CMake..."
# Use -DCMAKE_BUILD_TYPE=Release for optimized build
cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release

if [ $? -ne 0 ]; then
    echo "ERROR: CMake configuration failed."
    rm -rf "$BUILD_DIR" # Clean up on failure
    exit 1
fi
echo "Configuration complete."
echo "---"

# 3. Compile the project
echo "Compiling project..."
# Use -j to parallelize compilation
cmake --build "$BUILD_DIR" --target "$TARGET_NAME" -j$(nproc)

if [ $? -ne 0 ]; then
    echo "ERROR: Compilation failed."
    rm -rf "$BUILD_DIR" # Clean up on failure
    exit 1
fi
echo "Compilation complete."
echo "---"

# 4. Final verification and automatic clean up
if [ -f "./$TARGET_NAME" ]; then
    echo "SUCCESS: Executable './$TARGET_NAME' has been created in the root directory."
else
    echo "WARNING: Executable was not found in the root directory. Check CMakeLists.txt POST_BUILD step."
fi
echo "---"

# Automatically remove the build directory as requested
echo "Automatically removing build directory: $BUILD_DIR"
rm -rf "$BUILD_DIR"

echo "### Build Process Finished ###"