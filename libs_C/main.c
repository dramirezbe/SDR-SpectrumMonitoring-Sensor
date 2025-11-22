//main.c

#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <libgen.h>
#include <limits.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/inotify.h>
#include "Drivers/cJSON.h"
#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

st_uart LTE;
gp_uart GPS;

GPSCommand GPSInfo;

bool LTE_open = false;
bool GPS_open = false;

int get_exec_dir(char *out, size_t out_size) {
    char buf[PATH_MAX];
    ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (len == -1) return -1;               // errno ya contiene la razón
    if ((size_t)len >= sizeof(buf) - 1) return -1; // truncado inesperado

    buf[len] = '\0';

    // Opcional: canonicalizar para resolver enlaces simbólicos
    char real[PATH_MAX];
    if (!realpath(buf, real)) {
        // Si realpath falla, usamos buf tal cual
        strncpy(real, buf, sizeof(real));
        real[sizeof(real)-1] = '\0';
    }

    // Quitamos el componente final (nombre del ejecutable)
    char *last = strrchr(real, '/');
    if (!last) {
        // no contiene '/', improbable, devolvemos "."
        if (out_size < 2) return -1;
        strcpy(out, ".");
        return 0;
    }

    size_t dir_len = (size_t)(last - real);
    if (dir_len == 0) {
        // el ejecutable en la raíz "/"
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

    // hacemos una copia local porque vamos a manipularla
    if (len + 1 > PATH_MAX) return -1;
    char tmp[PATH_MAX];
    strncpy(tmp, path, sizeof(tmp));
    tmp[sizeof(tmp)-1] = '\0';

    // quitar barras de final
    while (strlen(tmp) > 1 && tmp[strlen(tmp)-1] == '/') tmp[strlen(tmp)-1] = '\0';

    char *last = strrchr(tmp, '/');
    if (!last) {
        // sin '/', parent es "."
        if (out_size < 2) return -1;
        strcpy(out, ".");
        return 0;
    }

    if (last == tmp) {
        // parent de "/algo" -> "/"
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

    // si base termina en '/', evitamos duplicar
    size_t b = strlen(base);
    int needs_slash = (b > 0 && base[b-1] != '/');

    // snprintf devuelve el número de bytes que habría escrito (sin contar '\0')
    int required = snprintf(out, out_size, "%s%s%s", base, needs_slash ? "/" : "", name);
    if (required < 0) return -1;
    if ((size_t)required >= out_size) return -1; // truncado
    return 0;
}

char* read_json(const char* filename) {
    FILE* f = fopen(filename, "rb");
    if (!f) return NULL;

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);

    char* buffer = malloc(size + 1);
    if (!buffer) {
        fclose(f);
        return NULL;
    }

    fread(buffer, 1, size, f);
    buffer[size] = '\0';
    fclose(f);
    return buffer;
}

/* Ejemplo de uso */
int main(void) {
    char exec_dir[PATH_MAX];
    char project_root[PATH_MAX];
    char persistent_json_path[PATH_MAX];

    if (get_exec_dir(exec_dir, sizeof(exec_dir)) != 0) {
        perror("get_exec_dir");
        return 1;
    }

    if (path_parent(exec_dir, project_root, sizeof(project_root)) != 0) {
        fprintf(stderr, "path_parent failed\n");
        return 1;
    }

    if (path_join(project_root, "persistent.json", persistent_json_path, sizeof(persistent_json_path)) != 0) {
        fprintf(stderr, "path_join failed\n");
        return 1;
    }

    int fd = inotify_init1(IN_NONBLOCK);
    if (fd < 0) {
        perror("inotify_init1");
        return 1;
    }

    int wd = inotify_add_watch(fd, persistent_json_path, IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO);
    if (wd < 0) {
        perror("inotify_add_watch");
        return 1;
    }

    char buf[4096]
        __attribute__ ((aligned(__alignof__(struct inotify_event))));
    ssize_t len;

    //(No tocar)
	if(status_LTE()) {
		printf("LTE module is ON\r\n");
	} else {
    	power_ON_LTE();
	}

    if(init_usart(&LTE) != 0)
    {
        printf("Error : LTE open failed\r\n");
        return -1;
    }

    printf("LTE module ready\r\n");
    //(No tocar)
    while(!LTE_Start(&LTE));
    printf("LTE response OK\n");
    //(No tocar)
    if(init_usart1(&GPS) != 0)
    {
        printf("Error : GPS open failed\r\n");
        return -1;
    }

    while (1)
    {
        len = read(fd, buf, sizeof(buf));
        if (len <= 0) {
            usleep(100000);
            continue;
        }

        for (char *ptr = buf; ptr < buf + len; ) {
            struct inotify_event *event = (struct inotify_event *)ptr;

            if (event->mask & IN_MODIFY ||
                event->mask & IN_CLOSE_WRITE ||
                event->mask & IN_MOVED_TO) {
                //File updated

                char* json_text = read_json(persistent_json_path);
                if (!json_text) {
                    printf("Error leyendo archivo JSON\n");
                    return 1;
                }

                cJSON* root = cJSON_Parse(json_text);
                free(json_text);

                if (!root) {
                    printf("Error parseando JSON: %s\n", cJSON_GetErrorPtr());
                    return 1;
                }


                cJSON* antenna = cJSON_GetObjectItem(root, "antenna_port");
                if (!cJSON_IsNumber(antenna)) {
                    printf("Error leyendo el puerto de la antena\n");
                    cJSON_Delete(root);
                    return 1;
                }

                int num_antenna = antenna->valueint;
                printf("antenna: %d\n", num_antenna);

                cJSON_Delete(root);

                select_ANTENNA(num_antenna);

            }

            ptr += sizeof(struct inotify_event) + event->len;
        }

        
        //(No tocar)
        printf ("Latitude = %s, Longitude = %s, Altitude = %s\n",GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
        sleep(3);
    }    

    close(fd);
    return 0;
}

