/**
 * @file Drivers/consumer.h
 */
#ifndef CONSUMER_H
#define CONSUMER_H

#include <stdint.h>
#include <stddef.h>
#include <pthread.h>
#include "ring_buffer.h"

// Function pointer for the specific logic (FM, CSV, etc.)
typedef void (*consumer_logic_fn)(const uint8_t *data, size_t len, void *ctx);

typedef struct {
    char name[32];
    ring_buffer_t rb;
    pthread_t thread;
    volatile int running;
    
    consumer_logic_fn logic_cb; // The callback
    void *ctx;                  // User data (File handle, PortAudio stream, etc.)
    
    size_t chunk_process_size;  // How many bytes to pull per loop
} Consumer_t;

void consumer_init(Consumer_t *c, const char *name, size_t buf_size, consumer_logic_fn cb, void *ctx);
void consumer_start(Consumer_t *c);
void consumer_stop(Consumer_t *c);
void consumer_push_chunk(Consumer_t *c, const uint8_t *data, size_t len);

#endif