// SPDX-License-Identifier: GPL-2.0-only
/* Read-only clock pair: BOOTTIME includes suspend, MONOTONIC excludes it. */
#include <inttypes.h>
#include <stdio.h>
#include <time.h>
static int64_t ns(struct timespec t) { return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec; }
int main(void)
{
    struct timespec a, b, c;
    if (clock_gettime(CLOCK_MONOTONIC, &a) || clock_gettime(CLOCK_BOOTTIME, &b) ||
        clock_gettime(CLOCK_MONOTONIC, &c)) return 1;
    printf("DRAIN_CLOCK %" PRId64 " %" PRId64 " %" PRId64 "\n",
           ns(b), ns(a) + (ns(c)-ns(a))/2, ns(c)-ns(a));
    return 0;
}
