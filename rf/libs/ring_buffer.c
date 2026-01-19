/**
 * @file ring_buffer.c
 * @brief Lógica de gestión de memoria y sincronización del búfer circular.
 */
#include "ring_buffer.h"

/* * Nota de implementación:
 * Esta implementación utiliza índices incrementales (head/tail) y aplica 
 * la operación módulo (%) sobre el tamaño del búfer para determinar el 
 * índice real del arreglo. Esto permite diferenciar entre un búfer 
 * completamente vacío y uno completamente lleno.
 */

void rb_init(ring_buffer_t *rb, size_t size) {
    // USE CALLOC: Allocates memory and automatically sets it to 0
    rb->buffer = calloc(1, size); 
    rb->size = size;
    rb->head = 0;
    rb->tail = 0;
    pthread_mutex_init(&rb->lock, NULL);
}

void rb_free(ring_buffer_t *rb) {
    if (rb->buffer) {
        // REQUESTED: Put to 0 (Secure Erase) before freeing
        memset(rb->buffer, 0, rb->size); 
        free(rb->buffer);
        rb->buffer = NULL;
    }
    pthread_mutex_destroy(&rb->lock);
}

// NEW: Helper to zero-out without freeing (if you optimize later)
void rb_reset(ring_buffer_t *rb) {
    pthread_mutex_lock(&rb->lock);
    if (rb->buffer) {
        memset(rb->buffer, 0, rb->size);
    }
    rb->head = 0;
    rb->tail = 0;
    pthread_mutex_unlock(&rb->lock);
}

/**
 * @internal
 * La función calcula el espacio disponible basándose en la diferencia
 * entre head y tail. Si la escritura requiere "dar la vuelta" al final
 * del arreglo, los datos se dividen en dos fragmentos (chunk1 y chunk2).
 */
size_t rb_write(ring_buffer_t *rb, const void *data, size_t len) {
    pthread_mutex_lock(&rb->lock);
    
    size_t space_free = rb->size - (rb->head - rb->tail);
    size_t to_write = MIN(len, space_free);

    if (to_write == 0) {
        pthread_mutex_unlock(&rb->lock);
        return 0;
    }

    // Circular logic using modulo
    size_t head_idx = rb->head % rb->size;
    size_t chunk1 = MIN(to_write, rb->size - head_idx);
    size_t chunk2 = to_write - chunk1;

    memcpy(rb->buffer + head_idx, data, chunk1);
    if (chunk2 > 0) memcpy(rb->buffer, (uint8_t*)data + chunk1, chunk2);

    rb->head += to_write;
    
    pthread_mutex_unlock(&rb->lock);
    return to_write;
}

/**
 * @internal
 * Al igual que la escritura, si los datos a leer cruzan el límite del
 * final del búfer físico, se realizan dos copias de memoria consecutivas.
 */
size_t rb_read(ring_buffer_t *rb, void *data, size_t len) {
    pthread_mutex_lock(&rb->lock);
    
    size_t available = rb->head - rb->tail;
    size_t to_read = MIN(len, available);

    if (to_read == 0) {
        pthread_mutex_unlock(&rb->lock);
        return 0;
    }

    size_t tail_idx = rb->tail % rb->size;
    size_t chunk1 = MIN(to_read, rb->size - tail_idx);
    size_t chunk2 = to_read - chunk1;

    memcpy(data, rb->buffer + tail_idx, chunk1);
    if (chunk2 > 0) memcpy((uint8_t*)data + chunk1, rb->buffer, chunk2);

    rb->tail += to_read;

    pthread_mutex_unlock(&rb->lock);
    return to_read;
}

size_t rb_available(ring_buffer_t *rb) {
    pthread_mutex_lock(&rb->lock);
    size_t val = rb->head - rb->tail;
    pthread_mutex_unlock(&rb->lock);
    return val;
}