#ifndef ZMQ_UTIL_H
#define ZMQ_UTIL_H

#include <zmq.h>
#include <pthread.h>

#define ZBUF_SIZE 65536 // Increased to handle larger JSON PSD arrays

typedef void (*msg_callback_t)(const char *payload);

typedef struct {
    void *context;
    void *socket;
    char *addr;
    char buffer[ZBUF_SIZE];
    pthread_t thread_id;
    msg_callback_t callback;
    volatile int running;
    int verbose;
} zpair_t;

zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose);
void zpair_start(zpair_t *pair);
int zpair_send(zpair_t *pair, const char *json_payload);
void zpair_close(zpair_t *pair);

#endif