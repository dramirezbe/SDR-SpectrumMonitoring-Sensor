cmake_minimum_required(VERSION 3.10)
project(RF_Project C)

# 1. Standard Settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# --------------------------------------------------------
# 2. COMMON LIBRARY (Shared Code + Shared Deps)
# --------------------------------------------------------
add_library(common_lib STATIC
    common/bacn_gpio.c
    common/bacn_gpio.h
)

target_include_directories(common_lib PUBLIC common)

# Use PUBLIC here so rf_app automatically links 
# to gpiod, cjson, m (math), and pthread.
target_link_libraries(common_lib PUBLIC 
    cjson 
    gpiod
    m           # Math
    pthread     # Threads
)

# --------------------------------------------------------
# 3. RF PROGRAM (Specific: HackRF, ZMQ, FFTW)
# --------------------------------------------------------
file(GLOB RF_LIB_SOURCES "rf/libs/*.c" "rf/libs/*.h")

add_executable(rf_metrics_app
    rf/rf_metrics.c
    ${RF_LIB_SOURCES}
)

target_include_directories(rf_metrics_app PRIVATE rf/libs)

# Only link libraries specific to RF here.
# (cjson, gpiod, m, and pthread are inherited from common_lib)
target_link_libraries(rf_metrics_app PRIVATE
    common_lib      
    zmq             
    hackrf          
    fftw3           
)