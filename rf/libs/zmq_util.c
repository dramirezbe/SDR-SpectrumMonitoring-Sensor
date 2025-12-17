#define _GNU_SOURCE
#include "zmq_util.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

#define WATCHDOG_TIMEOUT 10.0

// --- Helper: Get monotonic time in seconds ---
static double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

// --- Helper: Close old socket and create/connect a new one ---
static int internal_connect(zpair_t *pair) {
    // 1. Close existing socket if valid
    if (pair->socket) {
        if (pair->verbose) printf("[C-PAIR] Re-creating socket...\n");
        zmq_close(pair->socket);
        pair->socket = NULL;
    }

    // 2. Create new socket
    pair->socket = zmq_socket(pair->context, ZMQ_PAIR);
    if (!pair->socket) return -1;

    // 3. Set Linger to 0 (Crucial for clean restarts)
    int linger = 0;
    zmq_setsockopt(pair->socket, ZMQ_LINGER, &linger, sizeof(linger));

    // 4. Set Receive Timeout (e.g. 500ms) to allow checking the watchdog
    int timeout = 500; 
    zmq_setsockopt(pair->socket, ZMQ_RCVTIMEO, &timeout, sizeof(timeout));

    // 5. Connect
    int rc = zmq_connect(pair->socket, pair->addr);
    
    if (rc == 0) {
        if (pair->verbose) printf("[C-PAIR] Connected to %s\n", pair->addr);
    } else {
        if (pair->verbose) fprintf(stderr, "[C-PAIR] Failed to connect to %s (Will retry)\n", pair->addr);
    }

    return rc;
}

static void* listener_thread(void *arg) {
    zpair_t *pair = (zpair_t*)arg;
    
    printf("[C-PAIR] Listener thread started (Watchdog: %.1fs).\n", WATCHDOG_TIMEOUT);

    double last_msg_time = get_time_sec();

    while (pair->running) {
        // Blocks for max 500ms (RCVTIMEO)
        int len = zmq_recv(pair->socket, pair->buffer, ZBUF_SIZE - 1, 0);
        double now = get_time_sec();

        if (len > 0) {
            // --- SUCCESS ---
            pair->buffer[len] = '\0';
            last_msg_time = now; // Reset Watchdog

            if (pair->verbose) {
                printf("[C-PAIR] << RECV from Py: %s\n", pair->buffer);
            }

            if (pair->callback) {
                pair->callback(pair->buffer);
            }
        } 
        else {
            // --- TIMEOUT / NO DATA ---
            // Check if we exceeded the watchdog limit
            if ((now - last_msg_time) > WATCHDOG_TIMEOUT) {
                if (pair->verbose) {
                    fprintf(stderr, "[C-PAIR] ⚠️  Watchdog Triggered (%.1fs silence). Reconnecting...\n", (now - last_msg_time));
                }
                
                // Force Reconnection
                internal_connect(pair);
                
                // Reset timer so we don't spam reconnections instantly
                last_msg_time = get_time_sec();
            }
        }
    }
    return NULL;
}

zpair_t* zpair_init(const char *ipc_addr, msg_callback_t cb, int verbose) {
    if (!ipc_addr) return NULL;

    zpair_t *pair = malloc(sizeof(zpair_t));
    if (!pair) return NULL;
    
    // Zero out the struct
    memset(pair, 0, sizeof(zpair_t));

    pair->addr = strdup(ipc_addr); // Save address for reconnections
    pair->context = zmq_ctx_new();
    pair->callback = cb;
    pair->verbose = verbose;

    // Initial Connection
    if (internal_connect(pair) != 0) {
        fprintf(stderr, "[C-PAIR] Warning: Initial connection failed. Background thread will retry.\n");
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

    int len = strlen(json_payload);
    // Use DONTWAIT so we don't hang main thread if socket is rebuilding
    int bytes_sent = zmq_send(pair->socket, json_payload, len, ZMQ_DONTWAIT);

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
        if (pair->addr) free(pair->addr);
        
        free(pair);
        printf("[C-PAIR] Closed.\n");
    }
}