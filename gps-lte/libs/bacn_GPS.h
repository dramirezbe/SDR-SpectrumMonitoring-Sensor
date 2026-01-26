/**
 * @file bacn_GPS.h
 * @brief Controlador para la adquisición y parseo de datos NMEA desde un receptor GPS.
 * * Este módulo gestiona la lectura asíncrona de tramas GPS y extrae información
 * relevante como latitud, longitud y altitud para su posterior envío.
 */

#ifndef BACN_GPS_H
#define BACN_GPS_H

#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <stdint.h>
#include <sys/select.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

/**
 * @defgroup bacn_gps_module GPS Module
 * @ingroup gps_binary
 * @brief Controlador para la adquisición y parseo de datos NMEA desde un receptor GPS.
 * @{
 */

/** @brief Tamaño del buffer de lectura para tramas NMEA. */
#define UART_BUFFER_SIZE    120

/** @brief Ruta persistente del dispositivo USB-Serial para el GPS. */
#define SERIAL_DEV_GPS "/dev/serial/by-id/usb-SimTech__Incorporated_SimTech__Incorporated_0123456789ABCDEF-if01-port0"

/**
 * @brief Estructura de control para la interfaz UART del GPS.
 */
typedef struct
{
    uint32_t serial_fd;     /**< Descriptor de archivo del dispositivo serie. */
    pthread_t th_recv;      /**< Hilo dedicado para la escucha del puerto. */
    int32_t recv_buff_cnt;  /**< Cantidad de bytes leídos en el último evento. */
} gp_uart;

/**
 * @brief Estructura de datos para almacenar una trama GPGGA parseada.
 * * Esta estructura contiene punteros a los segmentos de la cadena NMEA 
 * procesada en @ref GPS_Track.
 */
typedef struct gps_command_s
{
    char* Header;       /**< Cabecera de la trama (ej. $GPGGA). */
    char* UTC_Time;     /**< Tiempo universal coordinado. */
    char* Latitude;     /**< Valor de latitud. */
    char* LatDir;       /**< Dirección de latitud (N/S). */
    char* Longitude;    /**< Valor de longitud. */
    char* LonDir;       /**< Dirección de longitud (E/W). */
    char* Quality;      /**< Calidad del fix GPS (0=invalido, 1=GPS fix). */
    char* Satelites;    /**< Número de satélites en uso. */
    char* HDOP;         /**< Dilución de precisión horizontal. */
    char* Altitude;     /**< Altitud sobre el nivel del mar. */
    char* Units_al;     /**< Unidades de altitud (M). */
    char* Undulation;   /**< Separación geoidal. */
    char* Units_un;     /**< Unidades de ondulación (M). */
    char* Age;          /**< Edad de los datos DGPS. */
    char* Cheksum;      /**< Checksum de la trama para validación. */
} GPSCommand;

/* --- Funciones de Control --- */

/**
 * @brief Inicializa el puerto serie del GPS y lanza el hilo de recepción.
 * @param s_uart Puntero a la estructura de control gp_uart.
 * @return 0 en éxito, -1 en caso de fallo.
 */
int8_t init_usart1(gp_uart *s_uart);

/**
 * @brief Detiene el hilo de recepción y cierra el descriptor de la UART.
 * @param s_uart Puntero a la estructura de control.
 */
void close_usart1(gp_uart *s_uart);

/**
 * @brief Tokeniza una cadena NMEA y asigna los valores a la estructura global GPSInfo.
 * @param GPSData Cadena de texto con la trama cruda recibida.
 */
void GPS_Track(char* GPSData);

/**
 * @brief Hilo de ejecución para la captura de datos del puerto serie.
 * @details Utiliza select() para monitoreo no bloqueante y dispara el parseo 
 * si la trama recibida tiene una longitud mínima válida.
 */
void* GPSIntHandler(void *arg);

/** @} */

#endif // BACN_GPS_H