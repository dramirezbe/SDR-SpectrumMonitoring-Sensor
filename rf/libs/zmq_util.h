#ifndef ZMQ_UTIL_H
#define ZMQ_UTIL_H

#include <zmq.h>
#include <pthread.h>

#define ZBUF_SIZE 4096

// Callback for when C receives a command from Python
typedef void (*msg_callback_t)(const char *payload);

typedef struct {
    void *context;
    void *socket;
    char buffer[ZBUF_SIZE];
    pthread_t thread_id;
    msg_callback_t callback;
    int running;
    int verbose;
} zpair_t;

/**
 * Initialize the PAIR socket.
 * @param ipc_addr The full address string (e.g., "ipc:///tmp/engine_pair")
 * @param cb Callback function for received messages
 * @param verbose Enable debug logging
 */
zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose);

void zpair_start(zpair_t *pair);
int zpair_send(zpair_t *pair, const char *json_payload);
void zpair_close(zpair_t *pair);

#endif