/**
 * @file net_audio_retry.h
 * @brief Gestión de reintentos y robustez para la transmisión de audio sobre TCP.
 *
 * Este módulo proporciona utilidades para manejar conexiones persistentes,
 * envío de datos garantizado y mecanismos de reconexión automática en caso de fallos.
 */


#ifndef NET_AUDIO_RETRY_H
#define NET_AUDIO_RETRY_H

#include "opus_tx.h"
#include "audio_stream_ctx.h"

#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stddef.h>
#include <stdbool.h>

/**
 * @defgroup net_audio_retry_module Net Audio Retry
 * @ingroup rf_binary
 * @brief Módulo para la transmision de audio sobre TCP
 * @{
 */

//typedef struct audio_stream_ctx audio_stream_ctx_t;


/** * @brief Retraso estándar entre intentos de reconexión en milisegundos.
 */
#define RECONNECT_DELAY_MS 1000

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Establece una conexión TCP de forma bloqueante con resolución de nombres.
 * * Utiliza @c getaddrinfo para soportar IPv4/IPv6 y configura el socket con
 * timeouts y Keep-Alive antes de intentar la conexión.
 *
 * @param[in] host Cadena con la dirección IP o nombre del host.
 * @param[in] port Puerto TCP de destino.
 * @return int Descriptor del socket (fd) si tiene éxito, o -1 en caso de error.
 */
int connect_tcp_net_audio(const char *host, int port);

/**
 * @brief Envía un bloque de datos completo, manejando envíos parciales e interrupciones.
 * * Itera sobre la llamada @c send hasta que todos los bytes solicitados hayan sido
 * transmitidos o ocurra un error irrecuperable.
 *
 * @param[in] fd  Descriptor del socket activo.
 * @param[in] buf Puntero al buffer de datos.
 * @param[in] len Longitud en bytes de los datos a enviar.
 * @return int 0 si se envió todo el bloque, -1 en caso de error o desconexión.
 */
int send_all_net_audio(int fd, const void *buf, size_t len);

/**
 * @brief Realiza una pausa en la ejecución que puede ser interrumpida externamente.
 * * Divide el tiempo de espera en pequeños intervalos para verificar frecuentemente 
 * el estado de un flag de control, permitiendo una finalización rápida del hilo.
 *
 * @param[in] total_ms    Tiempo total de espera en milisegundos.
 * @param[in] running_flag Puntero volátil a una bandera de control; si cambia a false, el sueño termina.
 */
void sleep_cancelable_ms(int total_ms, volatile bool *running_flag);



/**
 * @brief Asegura que el transmisor Opus esté conectado, reintentando si es necesario.
 * * Si el puntero al transmisor (*ptx) es nulo, entra en un bucle de reintento hasta que
 * logra establecer la conexión o hasta que el flag de ejecución se apague.
 *
 * @param[in]  ctx          Contexto que contiene los parámetros de audio y red.
 * @param[out] ptx          Doble puntero donde se almacenará la instancia de @ref opus_tx_t creada.
 * @param[in]  running_flag Bandera que controla la continuidad del bucle de reintentos.
 * @return int 0 si el transmisor está listo/conectado, -1 si el proceso fue cancelado.
 */
int ensure_tx_with_retry(audio_stream_ctx_t *ctx, opus_tx_t **ptx, volatile bool *running_flag);

/** @} */

#ifdef __cplusplus
}
#endif

#endif // NET_AUDIO_RETRY_H