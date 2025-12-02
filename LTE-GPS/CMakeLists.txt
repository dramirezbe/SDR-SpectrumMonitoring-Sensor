cmake_minimum_required(VERSION 3.10)

# Project Name
project(BACN_GPS_LTE C)

# Set C Standard (C99 or C11 is recommended for embedded)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED True)

# -----------------------------------------------------------------------------
# 1. Find Dependencies
# -----------------------------------------------------------------------------

# Find Pthread (Standard CMake method)
set(CMAKE_THREAD_PREFER_PTHREAD TRUE)
find_package(Threads REQUIRED)

# Find libgpiod
# We search for the library specifically. 
find_library(GPIOD_LIB gpiod)

if(NOT GPIOD_LIB)
    message(FATAL_ERROR "libgpiod not found! Please install it (e.g., sudo apt install libgpiod-dev)")
else()
    message(STATUS "Found libgpiod: ${GPIOD_LIB}")
endif()

# -----------------------------------------------------------------------------
# 2. Define Sources
# -----------------------------------------------------------------------------

set(SOURCES
    main.c
    Drivers/bacn_gpio.c
    Drivers/bacn_GPS.c
    Drivers/bacn_LTE.c
)

# -----------------------------------------------------------------------------
# 3. Create Executable
# -----------------------------------------------------------------------------

add_executable(bacn_app ${SOURCES})

# -----------------------------------------------------------------------------
# 4. Include Paths & Linking
# -----------------------------------------------------------------------------

# Add the current root directory to includes so #include "Drivers/..." works
target_include_directories(bacn_app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

# Link the libraries found earlier
target_link_libraries(bacn_app PRIVATE 
    Threads::Threads 
    ${GPIOD_LIB}
)