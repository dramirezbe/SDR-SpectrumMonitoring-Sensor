/**
 * @file Drivers/zmqpub.c
 */

#include "zmqpub.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

zpub_t* zpub_init(void) {
    zpub_t *pub = malloc(sizeof(zpub_t));
    if (!pub) return NULL;

    pub->context = zmq_ctx_new();
    pub->socket = zmq_socket(pub->context, ZMQ_PUB);

    // BINDING: The Publisher creates the file. 
    // The Python Subscriber will CONNECT to this.
    int rc = zmq_bind(pub->socket, PUB_IPC_ADDR);
    
    if (rc != 0) {
        fprintf(stderr, "[ZPUB] Error: Could not bind to %s. (Is another process holding it?)\n", PUB_IPC_ADDR);
        free(pub);
        return NULL;
    }

    printf("[ZPUB] Publisher bound to %s\n", PUB_IPC_ADDR);
    return pub;
}

int zpub_publish(zpub_t *pub, const char *topic, const char *json_payload) {
    if (!pub || !topic || !json_payload) return -1;

    // 1. Calculate required length: Topic + Space + JSON + NullTerminator
    int len = snprintf(NULL, 0, "%s %s", topic, json_payload);
    if (len < 0) return -1;

    // 2. Allocate buffer
    char *buffer = malloc(len + 1);
    if (!buffer) return -1;

    // 3. Format the string: "topic {json}"
    snprintf(buffer, len + 1, "%s %s", topic, json_payload);

    // 4. Send
    // We send as a single string to match the Python split(" ", 1) logic
    int bytes_sent = zmq_send(pub->socket, buffer, len, 0);

    free(buffer);
    return bytes_sent;
}

void zpub_close(zpub_t *pub) {
    if (pub) {
        if (pub->socket) zmq_close(pub->socket);
        if (pub->context) zmq_ctx_term(pub->context);
        free(pub);
        printf("[ZPUB] Publisher closed.\n");
    }
}