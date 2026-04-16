/**
 * @file ring_buffer.c
 * @brief Lógica de gestión de memoria y sincronización del búfer circular.
 */
#include "ring_buffer.h"

/**
 * @addtogroup rb_module
 * @{
 */

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
    atomic_init(&rb->head, 0);
    atomic_init(&rb->tail, 0);
}

void rb_free(ring_buffer_t *rb) {
    if (rb->buffer) {
        // REQUESTED: Put to 0 (Secure Erase) before freeing
        memset(rb->buffer, 0, rb->size); 
        free(rb->buffer);
        rb->buffer = NULL;
    }
}

// NEW: Helper to zero-out without freeing (if you optimize later)
void rb_reset(ring_buffer_t *rb) {
    if (rb->buffer) {
        memset(rb->buffer, 0, rb->size);
    }
    atomic_store_explicit(&rb->tail, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->head, 0, memory_order_release);
}

/**
 * @internal
 * La función calcula el espacio disponible basándose en la diferencia
 * entre head y tail. Si la escritura requiere "dar la vuelta" al final
 * del arreglo, los datos se dividen en dos fragmentos (chunk1 y chunk2).
 */
size_t rb_write(ring_buffer_t *rb, const void *data, size_t len) {
    const size_t head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    const size_t tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
    size_t space_free = rb->size - (head - tail);
    size_t to_write = MIN(len, space_free);

    if (to_write == 0) {
        return 0;
    }

    // Circular logic using modulo
    size_t head_idx = head % rb->size;
    size_t chunk1 = MIN(to_write, rb->size - head_idx);
    size_t chunk2 = to_write - chunk1;

    memcpy(rb->buffer + head_idx, data, chunk1);
    if (chunk2 > 0) memcpy(rb->buffer, (uint8_t*)data + chunk1, chunk2);

    atomic_store_explicit(&rb->head, head + to_write, memory_order_release);
    return to_write;
}

/**
 * @internal
 * Al igual que la escritura, si los datos a leer cruzan el límite del
 * final del búfer físico, se realizan dos copias de memoria consecutivas.
 */
size_t rb_read(ring_buffer_t *rb, void *data, size_t len) {
    const size_t tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    const size_t head = atomic_load_explicit(&rb->head, memory_order_acquire);
    size_t available = head - tail;
    size_t to_read = MIN(len, available);

    if (to_read == 0) {
        return 0;
    }

    size_t tail_idx = tail % rb->size;
    size_t chunk1 = MIN(to_read, rb->size - tail_idx);
    size_t chunk2 = to_read - chunk1;

    memcpy(data, rb->buffer + tail_idx, chunk1);
    if (chunk2 > 0) memcpy((uint8_t*)data + chunk1, rb->buffer, chunk2);

    atomic_store_explicit(&rb->tail, tail + to_read, memory_order_release);
    return to_read;
}

size_t rb_available(ring_buffer_t *rb) {
    const size_t head = atomic_load_explicit(&rb->head, memory_order_acquire);
    const size_t tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
    return head - tail;
}

/** @} */
