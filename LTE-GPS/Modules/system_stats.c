/**
 * @file Modules/system_stats.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/sysinfo.h>
#include <sys/statvfs.h>
#include "system_stats.h"

// Internal helper for CPU parsing
static void get_cpu_jiffies(unsigned long long *total, unsigned long long *work, int core_id) {
    FILE *fp = fopen("/proc/stat", "r");
    char line[256];
    char search[10];
    sprintf(search, "cpu%d ", core_id);

    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, search, strlen(search)) == 0) {
            unsigned long long u, n, s, i, io, irq, softirq;
            sscanf(line + strlen(search), "%llu %llu %llu %llu %llu %llu %llu", 
                   &u, &n, &s, &i, &io, &irq, &softirq);
            *work = u + n + s + irq + softirq;
            *total = *work + i + io;
            break;
        }
    }
    fclose(fp);
}

void get_system_stats(SystemStats *stats) {
    struct sysinfo si;
    struct statvfs disk;
    
    // RAM & Swap
    sysinfo(&si);
    stats->ram_total_mb = (si.totalram * si.mem_unit) / 1048576;
    stats->ram_used_mb = ((si.totalram - si.freeram) * si.mem_unit) / 1048576;
    stats->swap_total_mb = (si.totalswap * si.mem_unit) / 1048576;
    stats->swap_used_mb = ((si.totalswap - si.freeswap) * si.mem_unit) / 1048576;

    // Disk
    statvfs("/", &disk);
    unsigned long long total_bytes = disk.f_blocks * disk.f_frsize;
    unsigned long long free_bytes = disk.f_bfree * disk.f_frsize;
    stats->disk_total_mb = total_bytes / 1048576;
    stats->disk_used_mb = (total_bytes - free_bytes) / 1048576;

    // Temperature
    FILE *ft = fopen("/sys/class/thermal/thermal_zone0/temp", "r");
    if (ft) {
        int temp_milli;
        fscanf(ft, "%d", &temp_milli);
        stats->temp_c = temp_milli / 1000.0;
        fclose(ft);
    } else {
        stats->temp_c = -1.0; 
    }

    // CPU (100ms delta)
    stats->cpu_count = sysconf(_SC_NPROCESSORS_ONLN);
    if (stats->cpu_count > MAX_CORES) stats->cpu_count = MAX_CORES;

    unsigned long long t1[MAX_CORES], w1[MAX_CORES];
    unsigned long long t2[MAX_CORES], w2[MAX_CORES];

    for (int i = 0; i < stats->cpu_count; i++) get_cpu_jiffies(&t1[i], &w1[i], i);
    usleep(100000); 
    for (int i = 0; i < stats->cpu_count; i++) get_cpu_jiffies(&t2[i], &w2[i], i);

    for (int i = 0; i < stats->cpu_count; i++) {
        stats->cpu_percent[i] = (float)(w2[i] - w1[i]) / (t2[i] - t1[i]) * 100.0;
    }
}

/**
 * Usage:
 * get_system_stats(&stats);
    printf("RAM:  %ld/%ld MB\n", stats.ram_used_mb, stats.ram_total_mb);
    printf("Swap: %ld/%ld MB\n", stats.swap_used_mb, stats.swap_total_mb);
    printf("Disk: %ld/%ld MB\n", stats.disk_used_mb, stats.disk_total_mb);
    printf("Temp: %.1f C\n", stats.temp_c);
    
    printf("CPUs: ");
    for (int i = 0; i < stats.cpu_count; i++) {
        printf("[%.1f%%] ", stats.cpu_percent[i]);
    }
    printf("\n");
 */