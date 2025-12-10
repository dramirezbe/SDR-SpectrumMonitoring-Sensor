/**
 * @file Drivers/zmqsub.h
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include "zmqsub.h"

static void* listener_thread(void *arg) {
    zsub_t *sub = (zsub_t*)arg;
    
    while (sub->running) {
        // Attempt to receive (blocks for max 1 second due to RCVTIMEO)
        int len = zmq_recv(sub->socket, sub->buffer, ZSUB_BUF_SIZE - 1, 0);
        
        if (len > 0) {
            sub->buffer[len] = '\0';
            char *json_payload = strchr(sub->buffer, ' ');
            if (json_payload && sub->callback) {
                sub->callback(json_payload + 1);
            }
        } else {
            // This else block runs when we time out. 
            // The thread is "awake" here but found no data.
            // It allows the while(sub->running) check to happen.
        }
    }
    return NULL;
}

zsub_t* zsub_init(const char *topic, msg_callback_t cb) {
    zsub_t *sub = malloc(sizeof(zsub_t));
    sub->context = zmq_ctx_new();
    sub->socket = zmq_socket(sub->context, ZMQ_SUB);
    sub->callback = cb;
    sub->running = 0;

    // FIX 1: Set a 1-second timeout so the thread wakes up to check 'running' flag
    int timeout = 1000; 
    zmq_setsockopt(sub->socket, ZMQ_RCVTIMEO, &timeout, sizeof(timeout));

    zmq_connect(sub->socket, IPC_ADDR);
    zmq_setsockopt(sub->socket, ZMQ_SUBSCRIBE, topic, strlen(topic));
    
    return sub;
}

void zsub_start(zsub_t *sub) {
    sub->running = 1;
    pthread_create(&sub->thread_id, NULL, listener_thread, sub);
    printf("[ZMQ] Background thread started.\n");
}

void zsub_close(zsub_t *sub) {
    sub->running = 0; // Signal thread to stop
    pthread_join(sub->thread_id, NULL); // Wait for current timeout cycle to finish
    zmq_close(sub->socket);
    zmq_ctx_term(sub->context);
    free(sub);
}

/**
 * Usage:
 * void handle_message(const char *payload) {
    printf("\n>>> [CALLBACK] Message: %s\n", payload);
    }
    char *topic = "DATA_FEED";

    zsub_t *sub = zsub_init(topic, handle_message);
    zsub_start(sub); // Starts the background listener


    //You can do other things here while the listener runs in the background

    //finnaly
    zsub_close(sub);
 */