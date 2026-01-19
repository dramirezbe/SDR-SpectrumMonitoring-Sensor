/**
 * @file ring_buffer.h
 * @brief Implementación de un búfer circular (Ring Buffer) seguro para hilos.
 * * Proporciona una cola FIFO (First-In, First-Out) utilizando un búfer contiguo
 * de memoria y punteros de lectura/escritura que rotan.
 */

#ifndef RING_BUFFER_H
#define RING_BUFFER_H

#include <stdint.h>
#include <stddef.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/** * @brief Macro para obtener el valor mínimo entre dos números.
 * @internal
 */
#define MIN(a,b) ((a)<(b)?(a):(b))

/**
 * @struct ring_buffer_t
 * @brief Estructura de control del búfer circular.
 */
typedef struct {
    uint8_t *buffer;      /**< Puntero al bloque de memoria principal. */
    size_t size;          /**< Tamaño total del búfer en bytes. */
    size_t head;          /**< Índice/Posición de escritura acumulada. */
    size_t tail;          /**< Índice/Posición de lectura acumulada. */
    pthread_mutex_t lock; /**< Mutex para garantizar acceso atómico. */
} ring_buffer_t;

/**
 * @brief Inicializa el búfer circular y su mutex.
 * @param rb Puntero a la estructura del búfer.
 * @param size Capacidad deseada en bytes.
 */
void rb_init(ring_buffer_t *rb, size_t size);

/**
 * @brief Libera la memoria y destruye el mutex.
 * @note Realiza un borrado seguro de los datos (memset a 0) antes de liberar.
 * @param rb Puntero a la estructura a liberar.
 */
void rb_free(ring_buffer_t *rb);

/**
 * @brief Escribe datos en el búfer.
 * @param rb Puntero al búfer.
 * @param data Puntero a los datos de origen.
 * @param len Cantidad de bytes que se intentan escribir.
 * @return size_t Cantidad real de bytes escritos (puede ser menor a len si el búfer está lleno).
 */
size_t rb_write(ring_buffer_t *rb, const void *data, size_t len);

/**
 * @brief Lee datos del búfer.
 * @param rb Puntero al búfer.
 * @param data Puntero al destino donde copiar los datos.
 * @param len Cantidad de bytes que se desean leer.
 * @return size_t Cantidad real de bytes leídos (disponibles).
 */
size_t rb_read(ring_buffer_t *rb, void *data, size_t len);

/**
 * @brief Devuelve la cantidad de bytes disponibles para lectura.
 * @param rb Puntero al búfer.
 * @return size_t Bytes listos para ser leídos.
 */
size_t rb_available(ring_buffer_t *rb);

/**
 * @brief Reinicia los índices y limpia el contenido del búfer.
 * @param rb Puntero al búfer.
 */
void rb_reset(ring_buffer_t *rb);

#endif