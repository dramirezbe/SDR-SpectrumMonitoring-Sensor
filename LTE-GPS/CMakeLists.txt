cmake_minimum_required(VERSION 3.10)
project(ZMQ_Project)

#Find manually compiled libraries
link_directories(/usr/bin)

file(GLOB DRIVER_SOURCES "Drivers/*.c" "Drivers/*.h" "Modules/*.c" "Modules/*.h")

# Add root directory to includes so "Drivers/zmqsub.h" works
include_directories(${CMAKE_SOURCE_DIR})

# Create executable with main.c and found driver files
add_executable(main main.c ${DRIVER_SOURCES})

# Link zmq and pthread
target_link_libraries(main zmq pthread hackrf portaudio m fftw3 cjson gpiod)
