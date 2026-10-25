// SPDX-License-Identifier: GPL-2.0-only
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
int main(void)
{
    struct timespec boot, mono;
    uint64_t rate;
    if (clock_gettime(CLOCK_BOOTTIME, &boot) || clock_gettime(CLOCK_MONOTONIC, &mono))
        return 1;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(rate));
    printf("CLOCK boot_ns=%" PRIu64 " mono_ns=%" PRIu64 " counter_hz=%" PRIu64 "\n",
           (uint64_t)boot.tv_sec * 1000000000 + boot.tv_nsec,
           (uint64_t)mono.tv_sec * 1000000000 + mono.tv_nsec, rate);
    return 0;
}
