/**
 * @file zmq_util.h
 * @brief Utilidad de sockets ZeroMQ PAIR multihilo para comunicación de payloads JSON.
 *
 * Proporciona un envoltorio de alto nivel alrededor de ZMQ_PAIR para manejar la 
 * recepción de mensajes en segundo plano mediante un hilo pthread dedicado.
 */

#ifndef ZMQ_UTIL_H
#define ZMQ_UTIL_H

#include <zmq.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/** @brief Tamaño máximo del búfer de mensajes. */
#define ZBUF_SIZE 65536 

/**
 * @brief Tipo de callback para mensajes recibidos.
 * @param payload Cadena terminada en nulo recibida desde el socket.
 */
typedef void (*msg_callback_t)(const char *payload);

/**
 * @struct zpair_t
 * @brief Estructura de gestión para una conexión ZMQ y su hilo de escucha.
 */
typedef struct {
    void *context;          /**< Manejador del contexto ZeroMQ. */
    void *socket;           /**< Manejador del socket ZeroMQ. */
    char *addr;             /**< Cadena con la dirección del endpoint. */
    char buffer[ZBUF_SIZE]; /**< Búfer interno para datos entrantes. */
    pthread_t thread_id;    /**< ID del hilo para el receptor en segundo plano. */
    msg_callback_t callback;/**< Función de usuario a llamar al recibir datos. */
    volatile int running;   /**< Bandera atómica para el ciclo de vida del hilo. */
    int verbose;            /**< Bandera para habilitar logs por stderr. */
} zpair_t;

/**
 * @brief Reserva memoria e inicializa una nueva conexión ZMQ pair.
 * @param ipc_addr Dirección de conexión (ej. "ipc:///tmp/feed.ipc").
 * @param cb Callback definido por el usuario para procesar mensajes.
 * @param verbose Habilita o deshabilita la salida de errores por consola.
 * @return Puntero a zpair_t si tiene éxito, NULL en caso de fallo de memoria.
 */
zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose);

/**
 * @brief Inicia el hilo de escucha en segundo plano.
 * @param pair Puntero a la instancia de zpair_t inicializada.
 */
void zpair_start(zpair_t *pair);

/**
 * @brief Envía un payload JSON utilizando E/S no bloqueante.
 * @param pair Puntero a la instancia activa de zpair_t.
 * @param json_payload Cadena de texto a transmitir.
 * @return Número de bytes enviados, o -1 en caso de fallo (ej. EAGAIN).
 */
int zpair_send(zpair_t *pair, const char *json_payload);

/**
 * @brief Detiene el hilo de forma segura, cierra sockets y libera memoria.
 * @param pair Puntero a la instancia de zpair_t a destruir.
 */
void zpair_close(zpair_t *pair);

#endif