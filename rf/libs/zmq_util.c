#define _GNU_SOURCE
#include "zmq_util.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int internal_connect(zpair_t *pair) {
    if (pair->socket) zmq_close(pair->socket);

    pair->socket = zmq_socket(pair->context, ZMQ_PAIR);
    if (!pair->socket) return -1;

    int linger = 0;
    zmq_setsockopt(pair->socket, ZMQ_LINGER, &linger, sizeof(linger));

    // Automatic reconnection settings
    int reconnect_ivl = 100; // Start retrying in 100ms
    zmq_setsockopt(pair->socket, ZMQ_RECONNECT_IVL, &reconnect_ivl, sizeof(reconnect_ivl));
    
    int reconnect_ivl_max = 1000; // Max retry interval 1s
    zmq_setsockopt(pair->socket, ZMQ_RECONNECT_IVL_MAX, &reconnect_ivl_max, sizeof(reconnect_ivl_max));

    // Short timeout allows the thread to check the 'running' flag regularly
    int timeout = 1000; 
    zmq_setsockopt(pair->socket, ZMQ_RCVTIMEO, &timeout, sizeof(timeout));

    return zmq_connect(pair->socket, pair->addr);
}

static void* listener_thread(void *arg) {
    zpair_t *pair = (zpair_t*)arg;
    
    while (pair->running) {
        int len = zmq_recv(pair->socket, pair->buffer, ZBUF_SIZE - 1, 0);

        if (len >= 0) {
            pair->buffer[len] = '\0';
            if (pair->callback) pair->callback(pair->buffer);
        } else {
            int err = zmq_errno();
            if (err != EAGAIN && pair->running) {
                if (pair->verbose) fprintf(stderr, "[ZMQ] Recv error: %s\n", zmq_strerror(err));
            }
        }
    }
    return NULL;
}

zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose) {
    zpair_t *pair = calloc(1, sizeof(zpair_t));
    if (!pair) return NULL;

    pair->addr = strdup(ipc_addr);
    pair->context = zmq_ctx_new();
    pair->callback = cb;
    pair->verbose = verbose;

    if (internal_connect(pair) != 0) {
        if (verbose) fprintf(stderr, "[ZMQ] Initial connect queued for %s\n", ipc_addr);
    }
    return pair;
}

void zpair_start(zpair_t *pair) {
    if (!pair) return;
    pair->running = 1;
    pthread_create(&pair->thread_id, NULL, listener_thread, pair);
}

int zpair_send(zpair_t *pair, const char *json_payload) {
    if (!pair || !pair->socket || !json_payload) return -1;
    // We use ZMQ_DONTWAIT to ensure the DSP loop never stalls if the pipe is full
    printf("[RF]>>>>>zmq\n");
    return zmq_send(pair->socket, json_payload, strlen(json_payload), ZMQ_DONTWAIT);
}

void zpair_close(zpair_t *pair) {
    if (!pair) return;
    pair->running = 0;
    pthread_join(pair->thread_id, NULL);
    zmq_close(pair->socket);
    zmq_ctx_term(pair->context);
    free(pair->addr);
    free(pair);
}