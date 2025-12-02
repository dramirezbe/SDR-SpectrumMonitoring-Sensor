#!/bin/bash

# Define project name matching the CMake output
EXEC_NAME="bacn_app"

echo "--- Starting Clean Build Process ---"

# 1. Erase build directory if it exists
if [ -d "build" ]; then
    echo "Removing existing build directory..."
    rm -rf build
fi

# 2. Create build directory
echo "Creating new build directory..."
mkdir build

# 3. Enter build directory
cd build

# 4. Run CMake
echo "Running CMake..."
cmake ..

if [ $? -ne 0 ]; then
    echo "Error: CMake failed."
    exit 1
fi

# 5. Run Make
echo "Compiling..."
make

if [ $? -ne 0 ]; then
    echo "Error: Compilation failed."
    exit 1
fi

# 6. Move executable to project root
if [ -f "$EXEC_NAME" ]; then
    echo "Moving $EXEC_NAME to project root..."
    mv "$EXEC_NAME" ../"$EXEC_NAME"
    cd ..
    echo "--- Build Complete! ---"
    echo "Run using: ./$EXEC_NAME"
else
    echo "Error: Executable not found after build."
    exit 1
fi