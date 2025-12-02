cmake_minimum_required(VERSION 3.10)

project(BACN_PROJECT C)

# --- Configuration Variables ---
set(TARGET_NAME main)
set(CMAKE_C_FLAGS_RELEASE "${CMAKE_C_FLAGS_RELEASE} -Wall -Wextra -std=gnu99")
set(CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG} -Wall -Wextra -std=gnu99 -g")

# --- Source Files ---
set(SRC_DIR .)
set(DRIVER_DIR Drivers)

file(GLOB MAIN_SRC "${SRC_DIR}/main.c")
file(GLOB DRIVER_SRC
    "${DRIVER_DIR}/bacn_gpio.c"
    "${DRIVER_DIR}/bacn_GPS.c"
    "${DRIVER_DIR}/bacn_LTE.c"
)
set(SRCS ${MAIN_SRC} ${DRIVER_SRC})

# --- Compilation and Linking ---
add_executable(${TARGET_NAME} ${SRCS})

target_include_directories(${TARGET_NAME} PRIVATE
    ${DRIVER_DIR}
)

# Link the necessary libraries
target_link_libraries(${TARGET_NAME}
    PRIVATE
    pthread
    gpiod
    rt
)

# --- Post-Build Step (Moving the Binary) ---
# This copies the final executable from the build folder to the project root.
add_custom_command(
    TARGET ${TARGET_NAME}
    POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        $<TARGET_FILE:${TARGET_NAME}>
        ${CMAKE_SOURCE_DIR}/${TARGET_NAME}
    COMMENT "Copying executable to project root directory."
)
