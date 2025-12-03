/**
 * @file Modules/system_stats.h
 */
#ifndef SYSTEM_STATS_H
#define SYSTEM_STATS_H

#define MAX_CORES 16

typedef struct {
    long ram_total_mb;
    long ram_used_mb;
    long swap_total_mb;
    long swap_used_mb;
    long disk_total_mb;
    long disk_used_mb;
    float cpu_percent[MAX_CORES];
    int cpu_count;
    float temp_c;
} SystemStats;

void get_system_stats(SystemStats *stats);

#endif