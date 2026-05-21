/**
 * @file zmq_util.h
 * @brief Utilidad de sockets ZeroMQ REP para comunicación síncrona de payloads JSON.
 *
 * Proporciona un envoltorio de alto nivel alrededor de ZMQ_REP para manejar un
 * flujo estricto request/reply 1:1 sin colas de aplicación ni hilo listener.
 */

#ifndef ZMQ_UTIL_H
#define ZMQ_UTIL_H

#include <zmq.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/**
 * @defgroup zmq_module ZMQ
 * @ingroup rf_binary
 * @brief Utilidad de sockets ZeroMQ REP para comunicación síncrona de payloads JSON
 * @{
 */

/** @brief Tamaño máximo del búfer de mensajes. */
#define ZBUF_SIZE 65536 

/**
 * @struct zpair_t
 * @brief Estructura de gestión para una conexión ZMQ REP síncrona.
 */
typedef struct {
    void *context;          /**< Manejador del contexto ZeroMQ. */
    void *socket;           /**< Manejador del socket ZeroMQ. */
    char *addr;             /**< Cadena con la dirección del endpoint. */
    char buffer[ZBUF_SIZE]; /**< Búfer interno para datos entrantes. */
    int verbose;            /**< Bandera para habilitar logs por stderr. */
} zpair_t;

/**
 * @brief Reserva memoria e inicializa una nueva conexión ZMQ REP.
 * @param ipc_addr Dirección de conexión (ej. "ipc:///tmp/feed.ipc").
 * @param verbose Habilita o deshabilita la salida de errores por consola.
 * @return Puntero a zpair_t si tiene éxito, NULL en caso de fallo de memoria.
 */
zpair_t* zpair_init(const char *ipc_addr, int verbose);

/**
 * @brief Espera un request y lo guarda en `pair->buffer`.
 * @param pair Puntero a la instancia de zpair_t inicializada.
 * @return Número de bytes recibidos; `0` si venció el timeout; `-1` si falló.
 */
int zpair_recv(zpair_t *pair);

/**
 * @brief Recrea el socket para resetear el estado interno REQ/REP tras errores.
 * @param pair Puntero a la instancia activa de zpair_t.
 * @return `0` en éxito, `-1` en error.
 */
int zpair_reconnect(zpair_t *pair);

/**
 * @brief Envía un payload JSON como reply del request actual.
 * @param pair Puntero a la instancia activa de zpair_t.
 * @param json_payload Cadena de texto a transmitir.
 * @return Número de bytes enviados, o -1 en caso de fallo.
 */
int zpair_send(zpair_t *pair, const char *json_payload);

/**
 * @brief Cierra sockets y libera memoria.
 * @param pair Puntero a la instancia de zpair_t a destruir.
 */
void zpair_close(zpair_t *pair);

/** @} */ 

#endif
