#include "zmq_util.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h> 

static void* listener_thread(void *arg) {
    zpair_t *pair = (zpair_t*)arg;
    
    printf("[C-PAIR] Listener thread started.\n");

    while (pair->running) {
        // Blocks for max 1000ms (RCVTIMEO)
        int len = zmq_recv(pair->socket, pair->buffer, ZBUF_SIZE - 1, 0);
        
        if (len > 0) {
            pair->buffer[len] = '\0';
            
            if (pair->verbose) {
                printf("[C-PAIR] << RECV from Py: %s\n", pair->buffer);
            }

            if (pair->callback) {
                pair->callback(pair->buffer);
            }
        }
    }
    return NULL;
}

zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose) {
    if (!ipc_addr) return NULL;

    zpair_t *pair = malloc(sizeof(zpair_t));
    if (!pair) return NULL;

    pair->context = zmq_ctx_new();
    pair->socket = zmq_socket(pair->context, ZMQ_PAIR);
    pair->callback = cb;
    pair->running = 0;
    pair->verbose = verbose;

    // Set timeout to allow thread exit
    int timeout = 1000; 
    zmq_setsockopt(pair->socket, ZMQ_RCVTIMEO, &timeout, sizeof(timeout));

    // Connect using the passed address
    int rc = zmq_connect(pair->socket, ipc_addr);
    
    if (rc != 0) {
        fprintf(stderr, "[C-PAIR] Error: Could not connect to %s\n", ipc_addr);
        zmq_close(pair->socket);
        zmq_ctx_term(pair->context);
        free(pair);
        return NULL;
    }

    if (verbose) {
        printf("[C-PAIR] Connected to %s\n", ipc_addr);
    }
    
    return pair;
}

void zpair_start(zpair_t *pair) {
    if (!pair) return;
    pair->running = 1;
    pthread_create(&pair->thread_id, NULL, listener_thread, pair);
}

int zpair_send(zpair_t *pair, const char *json_payload) {
    if (!pair || !json_payload) return -1;

    int len = strlen(json_payload);
    int bytes_sent = zmq_send(pair->socket, json_payload, len, 0);

    if (pair->verbose && bytes_sent > 0) {
        printf("[C-PAIR] >> SENT to Py\n");
    }

    return bytes_sent;
}

void zpair_close(zpair_t *pair) {
    if (pair) {
        pair->running = 0;
        pthread_join(pair->thread_id, NULL);

        if (pair->socket) zmq_close(pair->socket);
        if (pair->context) zmq_ctx_term(pair->context);
        
        free(pair);
        printf("[C-PAIR] Closed.\n");
    }
}