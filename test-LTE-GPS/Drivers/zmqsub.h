/**
 * @file Drivers/zmqsub.h
 */
#ifndef ZMQSUB_H
#define ZMQSUB_H

#include <zmq.h>
#include <pthread.h>

#define IPC_ADDR "ipc:///tmp/zmq_feed"
#define ZSUB_BUF_SIZE 1024

// Define the type for the function you want to run when data arrives
typedef void (*msg_callback_t)(const char *payload);

typedef struct {
    void *context;
    void *socket;
    char buffer[ZSUB_BUF_SIZE];
    pthread_t thread_id;
    msg_callback_t callback; // Pointer to your processing function
    int running;
} zsub_t;

// Initialize
zsub_t* zsub_init(const char *topic, msg_callback_t cb);

// Start the background thread
void zsub_start(zsub_t *sub);

// Cleanup
void zsub_close(zsub_t *sub);

#endif