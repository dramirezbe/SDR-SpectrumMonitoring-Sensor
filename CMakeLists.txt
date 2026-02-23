cmake_minimum_required(VERSION 3.10)
project(RadioProject C)

# Find OpenMP
find_package(OpenMP REQUIRED)

# ==========================================
# Compiler & Settings
# ==========================================
add_compile_options(-Wall -Wextra)
include_directories(common)

# ==========================================
# Libraries
# ==========================================
set(LIBS_CORE zmq hackrf fftw3 m cjson curl opus OpenMP::OpenMP_C)
set(LIBS_FULL ${LIBS_CORE} gpiod)

# ==========================================
# Sources
# ==========================================
set(SRC_COMMON common/bacn_gpio.c)

# RF Sources
file(GLOB SRC_RF_LIBS "rf/libs/*.c")
set(SRC_RF_ALL rf/rf.c ${SRC_RF_LIBS})

# GPS Sources
file(GLOB SRC_GPS_LIBS "gps-lte/libs/*.c")
set(SRC_GPS_ALL gps-lte/gps-lte.c ${SRC_GPS_LIBS})

# ==========================================
# Logic Switch: Standard vs Standalone
# ==========================================
option(BUILD_STANDALONE "Build RF Only without common libs" OFF)

if(BUILD_STANDALONE)
    # --- STANDALONE BUILD (Matches 'rf_standalone' logic) ---
    # Output Name: rf_app
    # Sources: RF Only (No Common)
    # Defines: NO_COMMON_LIBS
    # Libs: Core Only (No GPIOD)
    
    add_executable(rf_app ${SRC_RF_ALL})
    target_include_directories(rf_app PRIVATE rf/libs)
    target_compile_definitions(rf_app PRIVATE NO_COMMON_LIBS)
    target_link_libraries(rf_app PRIVATE ${LIBS_CORE})

else()
    # --- STANDARD BUILD (Matches 'all' logic) ---
    
    # 1. RF Application
    # Output Name: rf_app
    # Sources: RF + Common
    # Libs: Full (Includes GPIOD)
    add_executable(rf_app ${SRC_RF_ALL} ${SRC_COMMON})
    target_include_directories(rf_app PRIVATE rf/libs)
    target_link_libraries(rf_app PRIVATE ${LIBS_FULL})

    # 2. GPS Application
    # Output Name: ltegps_app
    # Sources: GPS + Common
    # Libs: Full (Includes GPIOD)
    add_executable(ltegps_app ${SRC_GPS_ALL} ${SRC_COMMON})
    target_include_directories(ltegps_app PRIVATE gps-lte/libs)
    target_link_libraries(ltegps_app PRIVATE ${LIBS_FULL})

endif()