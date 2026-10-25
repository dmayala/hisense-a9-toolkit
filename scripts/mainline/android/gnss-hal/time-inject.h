/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdint.h>
#include <time.h>

/* Private anonymous pipe, same-host ABI; no paths or locations. Writes are
 * atomic (24 bytes < PIPE_BUF). ref_ms is Android elapsedRealtime, BOOTTIME,
 * never CLOCK_MONOTONIC (which excludes suspend). */
#define A9_TIME_MAGIC UINT32_C(0x41395431)
#define A9_TIME_MAX_AGE INT64_C(86400000)
struct a9_time_sample { int64_t utc_ms, ref_ms; uint32_t uncertainty_ms, magic; };
#ifdef __cplusplus
static_assert(sizeof(a9_time_sample)==24);
#else
_Static_assert(sizeof(struct a9_time_sample)==24, "time pipe record ABI");
#endif
static inline int64_t a9_boot_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_BOOTTIME, &t) || t.tv_sec < 0 || t.tv_sec > INT64_MAX/1000-1) return -1;
    return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static inline int a9_time_current(const struct a9_time_sample *s, int64_t now,
                                  uint64_t *utc, uint32_t *uncertainty) {
    if (s->magic != A9_TIME_MAGIC || now < 0 || s->ref_ms < 0 || s->ref_ms > now ||
        now-s->ref_ms > A9_TIME_MAX_AGE || s->uncertainty_ms > A9_TIME_MAX_AGE ||
        s->utc_ms < INT64_C(1577836800000) || s->utc_ms > INT64_C(4102444800000)) return 0;
    int64_t age=now-s->ref_ms;
    *utc=(uint64_t)(s->utc_ms+age);
    /* Conservative 50 ppm clock drift while a cached sample ages. */
    *uncertainty=s->uncertainty_ms+(uint32_t)((age+19999)/20000);
    return *utc <= UINT64_C(4102444800000);
}
