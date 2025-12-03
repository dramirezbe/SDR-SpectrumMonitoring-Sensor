#!/bin/bash

# Always remove build directory first
rm -rf build

# Create and enter build directory
mkdir build
cd build

# Compile
cmake ..
make

# Move executable to root
mv main ..