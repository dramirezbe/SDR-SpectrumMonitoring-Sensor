cmake_minimum_required(VERSION 3.10)
project(RF_LTEGPS_Project C)

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

# KEY CHANGE: Use PUBLIC here.
# This ensures that both rf_app and gps_lte_app automatically 
# get linked to gpiod, cjson, m (math), and pthread.
target_link_libraries(common_lib PUBLIC 
    cjson 
    gpiod
    m           # Math (Standard in both)
    pthread     # Threads (Standard in both)
)


# --------------------------------------------------------
# 3. RF PROGRAM (Specific: HackRF, ZMQ, FFTW)
# --------------------------------------------------------
file(GLOB RF_LIB_SOURCES "rf/libs/*.c" "rf/libs/*.h")

add_executable(rf_app
    rf/rf.c
    ${RF_LIB_SOURCES}
)

target_include_directories(rf_app PRIVATE rf/libs)

# Only link libraries specific to RF here.
# (cjson, gpiod, m, and pthread are inherited from common_lib)
target_link_libraries(rf_app PRIVATE
    common_lib      
    zmq             
    hackrf          
    fftw3           
)


# --------------------------------------------------------
# 4. GPS-LTE PROGRAM (Specific: CURL)
# --------------------------------------------------------
file(GLOB GPS_LIB_SOURCES "gps-lte/libs/*.c" "gps-lte/libs/*.h")

add_executable(gps_lte_app
    gps-lte/gps-lte.c
    ${GPS_LIB_SOURCES}
)

target_include_directories(gps_lte_app PRIVATE gps-lte/libs)

# Only link libraries specific to GPS/LTE here.
# (cjson, gpiod, m, and pthread are inherited from common_lib)
target_link_libraries(gps_lte_app PRIVATE
    common_lib      
    curl            
)