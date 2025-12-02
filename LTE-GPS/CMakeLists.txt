# Specify the minimum version of CMake required
cmake_minimum_required(VERSION 3.10)

# Define the project name
project(BACN_Driver C)

# Set the C standard (often C99 or C11 is suitable for this kind of code)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED True)

# Define the common C flags, mirroring your CFLAGS from the Makefile
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fPIC -O2 -Wall -Wextra")

# Define the include directories. -IDrivers is added here
include_directories(Drivers)

# Define the source files
set(SOURCE_FILES
    Drivers/bacn_LTE.c
    Drivers/bacn_GPS.c
    Drivers/bacn_gpio.c
    Drivers/bacn_api.c
)

# Define the name of the output library
set(LIBRARY_NAME "bacn")
set(LIBRARY_OUTPUT_NAME "lib${LIBRARY_NAME}.so")

# Create the shared library target
# The SHARED keyword makes it a .so file (equivalent to -shared in LDFLAGS)
add_library(${LIBRARY_NAME} SHARED ${SOURCE_FILES})

# Link the required libraries (-lpthread and -lgpiod)
target_link_libraries(${LIBRARY_NAME}
    pthread
    gpiod
)

# Set the output directory for the compiled library to the root of the project.
# By default, it would go into the 'build' directory, but we set it here
# so the install.sh script can easily move it, or you can run 'make' directly.
set_target_properties(${LIBRARY_NAME} PROPERTIES
    OUTPUT_NAME "libbacn" # CMake automatically adds the .so extension for SHARED libraries
    RUNTIME_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}"
    LIBRARY_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}"
)

# Note: The above OUTPUT_DIRECTORY settings ensure the final .so is placed
# directly in the root directory after a successful build inside the 'build' folder.