cmake_minimum_required(VERSION 3.10)
project(ZMQ_Project C)

# 1. Set C Standard (Recommended for C11/C99 features)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# 2. Source Management
# Grab all .c and .h files in subdirectories
file(GLOB DRIVER_SOURCES 
    "Drivers/*.c" 
    "Drivers/*.h" 
    "Modules/*.c" 
    "Modules/*.h"
)

# 3. Include Paths
# This ensures #include "Drivers/zmqsub.h" works from anywhere
include_directories(${CMAKE_SOURCE_DIR})

# 4. Create Executable
# IMPORTANT: We explicitly add functions.c here alongside main.c
add_executable(main 
    main.c
    ${DRIVER_SOURCES}
)

# 5. Libraries
# Note: I removed link_directories(/usr/bin) as libraries (.so/.a) 
# are rarely in /bin. Standard libs are found automatically.
target_link_libraries(main 
    zmq 
    pthread 
    hackrf 
    m           # Math library (essential for -lm)
    fftw3       # FFT library (Ensure your Welch method uses this)
    cjson 
    gpiod       # GPIO Driver
    curl        # CURL (Ensure your GPS HTTP post uses this)
)