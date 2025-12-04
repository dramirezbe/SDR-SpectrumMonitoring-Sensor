/**
 * @file Drivers/consumer.c
 */
#include "consumer.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

static void* consumer_worker(void* arg) {
    Consumer_t *c = (Consumer_t*)arg;
    uint8_t *temp_buf = malloc(c->chunk_process_size);
    
    printf("[%s] Thread Started\n", c->name);

    while (c->running) {
        size_t available = rb_available(&c->rb);
        
        // Only process if we have enough data (or a minimum threshold)
        if (available >= c->chunk_process_size) {
            size_t read = rb_read(&c->rb, temp_buf, c->chunk_process_size);
            if (read > 0 && c->logic_cb) {
                // Execute the registered logic
                c->logic_cb(temp_buf, read, c->ctx);
            }
        } else {
            // Prevent CPU spin
            usleep(1000); 
        }
    }
    
    free(temp_buf);
    printf("[%s] Thread Stopped\n", c->name);
    return NULL;
}

void consumer_init(Consumer_t *c, const char *name, size_t buf_size, consumer_logic_fn cb, void *ctx) {
    strncpy(c->name, name, 31);
    rb_init(&c->rb, buf_size);
    c->logic_cb = cb;
    c->ctx = ctx;
    c->running = 0;
    c->chunk_process_size = 4096; // Default processing block
}

void consumer_start(Consumer_t *c) {
    if (c->running) return;
    c->running = 1;
    pthread_create(&c->thread, NULL, consumer_worker, c);
}

void consumer_stop(Consumer_t *c) {
    c->running = 0;
    pthread_join(c->thread, NULL);
    rb_free(&c->rb);
}

void consumer_push_chunk(Consumer_t *c, const uint8_t *data, size_t len) {
    if (!c->running) return;
    // Push data into the consumer's private ring buffer
    // If buffer is full, this drops data (real-time behavior)
    rb_write(&c->rb, data, len); 
}