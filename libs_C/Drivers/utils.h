#ifndef UTILS_H
#define UTILS_H

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <sys/types.h>
#include <errno.h>
#include <sys/inotify.h>

typedef struct {
    char exec_dir[PATH_MAX];
    char project_root[PATH_MAX];
    char persistent_json_path[PATH_MAX];
} paths_t;

// --- CAMBIO IMPORTANTE: Añadido parámetro 'filename' al callback ---
typedef void (*inotify_callback_t)(const char *watch_path, const char *filename, uint32_t mask, void *user_data);

typedef struct {
    int wd;
    char *path;
    uint32_t mask;
    inotify_callback_t callback;
    void *user_data;
} WatchEntry;

typedef struct {
    int fd;
    WatchEntry *watches;
    size_t count;
    size_t capacity;
} InotifyManager;

// Funciones de rutas
int get_exec_dir(char *out, size_t out_size);
int path_join(const char *base, const char *name, char *out, size_t out_size);
int path_parent(const char *path, char *out, size_t out_size);
int fill_paths(paths_t* paths);

// Función de lectura
char* read_file_to_string(const char* filename);

// Funciones de Inotify
int inotify_manager_init(InotifyManager *manager);
void inotify_manager_cleanup(InotifyManager *manager);
int inotify_manager_add_watch(InotifyManager *manager, const char *path, uint32_t mask, inotify_callback_t callback, void *user_data);
int inotify_manager_process_events(InotifyManager *manager);

#endif