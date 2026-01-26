/**
 * @file bacn_LTE.h
 * @brief Controlador para la comunicación con módulos LTE vía comandos AT.
 * * Este módulo gestiona una interfaz serie (UART) para enviar comandos AT y
 * recibir respuestas de forma asíncrona mediante un hilo dedicado.
 */

#ifndef BACN_LTE_H
#define BACN_LTE_H

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
 * @defgroup bacn_lte_module LTE Module
 * @ingroup gps_binary
 * @brief Controlador para la comunicación con módulos LTE vía comandos AT.
 */

/** @brief Tamaño del buffer para las respuestas del módulo LTE. */
#define UART_BUFFER_SIZE    120
/** @brief Tiempo de espera por defecto para la respuesta (escalado para lógica interna). */
#define DEFAULT_TIMEOUT     4000 
/** @brief Cantidad de secuencias CRLF (\r\n) esperadas para dar por terminada una respuesta estándar. */
#define DEFAULT_CRLF_COUNT  2
/** @brief Ruta del dispositivo serie en el sistema. */
#define SERIAL_DEV "/dev/ttyAMA0"

/**
 * @brief Estados posibles de la respuesta del módulo LTE.
 */
enum LTE_RESPONSE_STATUS {
    LTE_RESPONSE_WAITING,       /**< Esperando datos del puerto serie. */
    LTE_RESPONSE_FINISHED,      /**< Respuesta completa recibida correctamente. */
    LTE_RESPONSE_TIMEOUT,       /**< Se alcanzó el tiempo límite de espera. */
    LTE_RESPONSE_BUFFER_FULL,   /**< El buffer de recepción ha excedido su capacidad. */
    LTE_RESPONSE_STARTING,      /**< Inicializando la máquina de estados de lectura. */
    LTE_RESPONSE_ERROR          /**< Error genérico en la comunicación o procesamiento. */
};

/**
 * @brief Estructura de control para la UART del LTE.
 */
typedef struct {
    uint32_t serial_fd;         /**< Descriptor de archivo del puerto serie. */
    pthread_t th_recv;          /**< Identificador del hilo de recepción asíncrona. */
    int32_t recv_buff_cnt;      /**< Contador de bytes recibidos en la última lectura. */
} st_uart;

/**
 * @brief Bloquea la ejecución hasta que se procesa una respuesta completa o expira el tiempo.
 */
void Read_Response(void);

/**
 * @brief Inicia el ciclo de lectura de respuesta, reintentando si el estado es de espera.
 */
void Start_Read_Response(void);

/**
 * @brief Espera a que el buffer contenga una cadena específica tras una recepción.
 * @param ExpectedResponse Cadena de texto que se busca (ej. "OK", "ERROR").
 * @return true Si se encontró la respuesta esperada.
 * @return false Si hubo timeout o la respuesta no coincide.
 */
bool WaitForExpectedResponse(const char* ExpectedResponse);

/**
 * @brief Envía un comando AT y verifica la respuesta esperada en un solo paso.
 * @param s_uart Puntero a la configuración de la UART.
 * @param ATCommand Comando a enviar (ej. "AT\r").
 * @param ExpectedResponse Respuesta buscada (ej. "OK").
 * @return true Si la operación fue exitosa.
 */
bool SendATandExpectResponse(st_uart *s_uart, const char* ATCommand, const char* ExpectedResponse);

/**
 * @brief Inicializa la comunicación con el módulo LTE y desactiva el Eco (ATE0).
 * @param s_uart Puntero a la configuración de la UART.
 * @return true Si el módulo responde correctamente tras los intentos.
 */
bool LTE_Start(st_uart *s_uart);

/**
 * @brief Envía una cadena de datos formateada hacia el módulo LTE.
 * @param s_uart Puntero a la configuración de la UART.
 * @param data Cadena de texto a enviar.
 */
void LTE_SendString(st_uart *s_uart, const char *data);

/**
 * @brief Configura el puerto serie e inicia el hilo de recepción.
 * @param s_uart Puntero a la estructura donde se guardará el descriptor y el hilo.
 * @return 0 en éxito, -1 en caso de error de apertura o configuración.
 */
int8_t init_usart(st_uart *s_uart);

/**
 * @brief Finaliza el hilo de recepción y cierra el puerto serie.
 * @param s_uart Puntero a la configuración de la UART.
 */
void close_usart(st_uart *s_uart);

/**
 * @brief Función ejecutada por el hilo para monitorear el puerto serie.
 * @param arg Puntero a st_uart pasado como argumento de hilo.
 */
void* LTEIntHandler(void *arg);

/** @} */

#endif // BACN_LTE_H