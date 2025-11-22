#define _POSIX_C_SOURCE 200809L
#include "utils.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/inotify.h>
#include <sys/types.h>

// --- Funciones de Rutas (Sin cambios) ---

int get_exec_dir(char *out, size_t out_size) {
    char buf[PATH_MAX];
    ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (len == -1) return -1;
    buf[len] = '\0';

    char real[PATH_MAX];
    if (!realpath(buf, real)) {
        strncpy(real, buf, sizeof(real));
        real[sizeof(real)-1] = '\0';
    }

    char *last = strrchr(real, '/');
    if (!last) {
        if (out_size < 2) return -1;
        strcpy(out, ".");
        return 0;
    }

    size_t dir_len = (size_t)(last - real);
    if (dir_len == 0) {
        if (out_size < 2) return -1;
        strcpy(out, "/");
        return 0;
    }

    if (dir_len + 1 > out_size) return -1;
    memcpy(out, real, dir_len);
    out[dir_len] = '\0';
    return 0;
}

int path_parent(const char *path, char *out, size_t out_size) {
    if (!path || !out) return -1;
    size_t len = strlen(path);
    if (len == 0) return -1;
    if (len + 1 > PATH_MAX) return -1;
    
    char tmp[PATH_MAX];
    strncpy(tmp, path, sizeof(tmp));
    tmp[sizeof(tmp)-1] = '\0';

    while (strlen(tmp) > 1 && tmp[strlen(tmp)-1] == '/') tmp[strlen(tmp)-1] = '\0';

    char *last = strrchr(tmp, '/');
    if (!last) {
        if (out_size < 2) return -1;
        strcpy(out, ".");
        return 0;
    }
    if (last == tmp) {
        if (out_size < 2) return -1;
        strcpy(out, "/");
        return 0;
    }
    *last = '\0';
    if (strlen(tmp) + 1 > out_size) return -1;
    strcpy(out, tmp);
    return 0;
}

int path_join(const char *base, const char *name, char *out, size_t out_size) {
    if (!base || !name || !out) return -1;
    size_t b = strlen(base);
    int needs_slash = (b > 0 && base[b-1] != '/');
    int required = snprintf(out, out_size, "%s%s%s", base, needs_slash ? "/" : "", name);
    if (required < 0 || (size_t)required >= out_size) return -1;
    return 0;
}

int fill_paths(paths_t* paths) {
    if (get_exec_dir(paths->exec_dir, sizeof(paths->exec_dir)) != 0) return 1;
    if (path_parent(paths->exec_dir, paths->project_root, sizeof(paths->project_root)) != 0) return 1;
    if (path_join(paths->project_root, "persistent.json", paths->persistent_json_path, sizeof(paths->persistent_json_path)) != 0) return 1;
    return 0;
}

char* read_file_to_string(const char* filename) {
    FILE* f = fopen(filename, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);
    char* buffer = malloc(size + 1);
    if (!buffer) { fclose(f); return NULL; }
    if (fread(buffer, 1, size, f) != (size_t)size) {
        free(buffer); fclose(f); return NULL;
    }
    buffer[size] = '\0';
    fclose(f);
    return buffer;
}

// --- Funciones de Inotify ---

static WatchEntry *inotify_manager_get_watch(InotifyManager *manager, int wd) {
    for (size_t i = 0; i < manager->count; i++) {
        if (manager->watches[i].wd == wd) return &manager->watches[i];
    }
    return NULL;
}

int inotify_manager_init(InotifyManager *manager) {
    manager->fd = inotify_init1(IN_NONBLOCK);
    if (manager->fd < 0) return -1;
    manager->watches = NULL;
    manager->count = 0;
    manager->capacity = 0;
    return 0;
}

void inotify_manager_cleanup(InotifyManager *manager) {
    if (manager->fd >= 0) {
        for (size_t i = 0; i < manager->count; i++) inotify_rm_watch(manager->fd, manager->watches[i].wd);
        close(manager->fd);
        manager->fd = -1;
    }
    for (size_t i = 0; i < manager->count; i++) free(manager->watches[i].path);
    free(manager->watches);
    manager->watches = NULL;
    manager->count = 0;
    manager->capacity = 0;
}

int inotify_manager_add_watch(InotifyManager *manager, const char *path, uint32_t mask, inotify_callback_t callback, void *user_data) {
    int wd = inotify_add_watch(manager->fd, path, mask);
    if (wd < 0) return -1;

    if (manager->count >= manager->capacity) {
        size_t new_capacity = manager->capacity == 0 ? 4 : manager->capacity * 2;
        WatchEntry *new_watches = realloc(manager->watches, new_capacity * sizeof(WatchEntry));
        if (!new_watches) { inotify_rm_watch(manager->fd, wd); return -1; }
        manager->watches = new_watches;
        manager->capacity = new_capacity;
    }

    WatchEntry *entry = &manager->watches[manager->count];
    entry->wd = wd;
    entry->path = strdup(path);
    entry->mask = mask;
    entry->callback = callback;
    entry->user_data = user_data;
    manager->count++;
    return 0;
}

#define EVENT_BUF_SIZE (sizeof(struct inotify_event) + NAME_MAX + 1) * 10

int inotify_manager_process_events(InotifyManager *manager) {
    char buf[EVENT_BUF_SIZE] __attribute__ ((aligned(__alignof__(struct inotify_event))));
    ssize_t len = read(manager->fd, buf, sizeof(buf));
    
    if (len == -1) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
        return -1;
    }

    for (char *ptr = buf; ptr < buf + len; ) {
        struct inotify_event *event = (struct inotify_event *)ptr;
        WatchEntry *entry = inotify_manager_get_watch(manager, event->wd);
        
        if (entry) {
            // --- CAMBIO IMPORTANTE: Extraemos el nombre del archivo ---
            const char *event_filename = (event->len > 0) ? event->name : NULL;
            // Pasamos el nombre del archivo al callback
            entry->callback(entry->path, event_filename, event->mask, entry->user_data);
        }

        ptr += sizeof(struct inotify_event) + event->len;
    }
    return 0;
}

