/**
 * @file zmq_util.c
 * @brief Detalles de implementación para la gestión síncrona de sockets ZMQ REP.
 */

#define _GNU_SOURCE
#include "zmq_util.h"

/**
 * @addtogroup zmq_module
 * @{
 */

/**
 * @brief Ayudante interno para configurar opciones de socket y conectar.
 * Configura un socket REP con timeouts cortos, HWM mínimo y reconexión automática.
 * @param pair La instancia a configurar.
 * @return Código de resultado ZMQ (0 éxito, -1 error).
 */
static int internal_connect(zpair_t *pair) {
    if (pair->socket) zmq_close(pair->socket);

    pair->socket = zmq_socket(pair->context, ZMQ_REP);
    if (!pair->socket) return -1;

    int linger = 0;
    zmq_setsockopt(pair->socket, ZMQ_LINGER, &linger, sizeof(linger));

    int immediate = 1;
    zmq_setsockopt(pair->socket, ZMQ_IMMEDIATE, &immediate, sizeof(immediate));

    int hwm = 1;
    zmq_setsockopt(pair->socket, ZMQ_SNDHWM, &hwm, sizeof(hwm));
    zmq_setsockopt(pair->socket, ZMQ_RCVHWM, &hwm, sizeof(hwm));

    // Automatic reconnection settings
    int reconnect_ivl = 100; // Start retrying in 100ms
    zmq_setsockopt(pair->socket, ZMQ_RECONNECT_IVL, &reconnect_ivl, sizeof(reconnect_ivl));
    
    int reconnect_ivl_max = 1000; // Max retry interval 1s
    zmq_setsockopt(pair->socket, ZMQ_RECONNECT_IVL_MAX, &reconnect_ivl_max, sizeof(reconnect_ivl_max));

    // Short timeout allows the thread to check the 'running' flag regularly
    int timeout = 1000; 
    zmq_setsockopt(pair->socket, ZMQ_RCVTIMEO, &timeout, sizeof(timeout));
    zmq_setsockopt(pair->socket, ZMQ_SNDTIMEO, &timeout, sizeof(timeout));

    return zmq_connect(pair->socket, pair->addr);
}

zpair_t* zpair_init(const char *ipc_addr, int verbose) {
    zpair_t *pair = calloc(1, sizeof(zpair_t));
    if (!pair) return NULL;

    pair->addr = strdup(ipc_addr);
    pair->context = zmq_ctx_new();
    pair->verbose = verbose;

    if (internal_connect(pair) != 0) {
        if (verbose) fprintf(stderr, "[ZMQ] Initial connect queued for %s\n", ipc_addr);
    }
    return pair;
}

int zpair_recv(zpair_t *pair) {
    if (!pair || !pair->socket) return -1;

    int len = zmq_recv(pair->socket, pair->buffer, ZBUF_SIZE - 1, 0);
    if (len >= 0) {
        pair->buffer[len] = '\0';
        return len;
    }

    int err = zmq_errno();
    if (err == EAGAIN) return 0;

    if (pair->verbose) fprintf(stderr, "[ZMQ] Recv error: %s\n", zmq_strerror(err));
    if (err == EFSM || err == ETERM) internal_connect(pair);
    return -1;
}

int zpair_reconnect(zpair_t *pair) {
    if (!pair || !pair->context) return -1;
    return internal_connect(pair);
}

int zpair_send(zpair_t *pair, const char *json_payload) {
    if (!pair || !pair->socket || !json_payload) return -1;

    int rc = zmq_send(pair->socket, json_payload, strlen(json_payload), 0);
    if (rc >= 0) {
        printf("[RF]>>>>>zmq\n");
        return rc;
    }

    if (pair->verbose) fprintf(stderr, "[ZMQ] Send error: %s\n", zmq_strerror(zmq_errno()));
    internal_connect(pair);
    return -1;
}

void zpair_close(zpair_t *pair) {
    if (!pair) return;
    if (pair->socket) zmq_close(pair->socket);
    if (pair->context) zmq_ctx_term(pair->context);
    free(pair->addr);
    free(pair);
}

/** @} */
