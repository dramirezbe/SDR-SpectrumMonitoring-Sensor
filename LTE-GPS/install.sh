#!/bin/bash

# --- Configuration ---
BUILD_DIR="build"
LIBRARY_NAME="libbacn.so"
PROJECT_ROOT=$(pwd)

echo "--- BACN Library Build Script ---"
echo "Project Root: ${PROJECT_ROOT}"

# 1. Clean up previous build files (equivalent to 'make clean' and removing the build directory)
echo "1. Cleaning previous build artifacts..."
rm -f "${PROJECT_ROOT}/${LIBRARY_NAME}"
rm -rf "${PROJECT_ROOT}/${BUILD_DIR}"
echo "   Previous build removed."

# 2. Create and change into the build directory
echo "2. Creating build directory: ${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

# 3. Configure the CMake project
# The '..' refers to the parent directory (project root) where CMakeLists.txt is located
echo "3. Configuring CMake project..."
cmake ..
if [ $? -ne 0 ]; then
    echo "ERROR: CMake configuration failed."
    exit 1
fi

# 4. Build the project
echo "4. Building the library: ${LIBRARY_NAME}"
cmake --build .
if [ $? -ne 0 ]; then
    echo "ERROR: CMake build failed."
    exit 1
fi

# The CMakeLists.txt is configured to place the output library directly
# in the project root, so we just need to confirm its existence.
if [ -f "${PROJECT_ROOT}/${LIBRARY_NAME}" ]; then
    echo "--- SUCCESS ---"
    echo "Library successfully built and available at: ${PROJECT_ROOT}/${LIBRARY_NAME}"
else
    echo "ERROR: Library ${LIBRARY_NAME} was not found after build."
    exit 1
fi

# 5. Return to the project root and clean the temporary build directory
cd "${PROJECT_ROOT}"
echo "5. Cleaning up temporary build directory..."
rm -rf "${BUILD_DIR}"
echo "   Cleanup complete."