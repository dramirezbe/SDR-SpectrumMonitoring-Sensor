#!/bin/bash

# Stop the script if any command fails (compilation errors)
set -e

# 1. Clean previous build artifacts
echo "Cleaning build directory..."
rm -rf build
rm -f rf_app
rm -f gps_lte_app

# 2. Create and enter build directory
mkdir build
cd build

# 3. Generate Makefiles and Compile
echo "Configuring CMake..."
cmake ..

echo "Compiling..."
make

# 4. Move the new executables to the root directory
echo "Moving executables to project root..."
mv rf_app ..
mv gps_lte_app ..

echo "Build Success! Created: ./rf_app and ./gps_lte_app"