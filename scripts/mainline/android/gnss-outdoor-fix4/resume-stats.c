// SPDX-License-Identifier: GPL-2.0-only
// Read-only, non-waking post-thaw RPM observer. Never programs an RTC/alarm.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#ifndef STATS_ROOT
#define STATS_ROOT "/sys/kernel/debug/qcom_stats"
#endif
#ifndef SUCCESS_PATH
#define SUCCESS_PATH "/sys/power/suspend_stats/success"
#endif
#ifndef KMSG_PATH
#define KMSG_PATH "/dev/kmsg"
#endif
#define NS UINT64_C(1000000000)
static int output = -1, output_failed;
static const char *session;

static uint64_t ns(clockid_t clock)
{
    struct timespec t;
    if (clock_gettime(clock, &t)) return UINT64_MAX;
    return (uint64_t)t.tv_sec * NS + (uint64_t)t.tv_nsec;
}

static int number(const char *s, uint64_t *value)
{
    char *end;
    while (*s == ' ' || *s == '\t') s++;
    if (*s < '0' || *s > '9') return -1;
    errno = 0;
    unsigned long long v = strtoull(s, &end, !strncmp(s, "0x", 2) ? 16 : 10);
    while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') end++;
    if (errno || *end) return -1;
    *value = v;
    return 0;
}

static void emit(const char *format, ...)
{
    char line[512];
    int head = snprintf(line, sizeof(line), "A9_SLEEP_OUT session=%s ", session);
    va_list args;
    va_start(args, format);
    int body = vsnprintf(line + head, sizeof(line) - (size_t)head, format, args);
    va_end(args);
    if (head < 0 || body < 0 || (size_t)(head + body + 1) >= sizeof(line)) {
        output_failed = 1;
        return;
    }
    size_t length = (size_t)(head + body);
    line[length++] = '\n';
    // One record per write: /dev/kmsg escapes newlines within a single write.
    ssize_t written;
    do { written = write(output, line, length); } while (written < 0 && errno == EINTR);
    if (written != (ssize_t)length) output_failed = 1;
}

static int success(uint64_t *value)
{
    char text[64];
    FILE *f = fopen(SUCCESS_PATH, "re");
    if (!f) return -1;
    int ok = fgets(text, sizeof(text), f) && !ferror(f);
    fclose(f);
    return ok ? number(text, value) : -1;
}

static int rpm(unsigned sample, const char *mode)
{
    static const char *labels[] = {"Count:", "Last Entered At:", "Last Exited At:",
                                 "Accumulated Duration:", "Client Votes:"};
    static const char *keys[] = {"count", "entered_ticks", "exited_ticks", "duration_ticks", "client_votes"};
    char path[256], line[256];
    uint64_t values[5] = {0};
    unsigned fields = 0;
    snprintf(path, sizeof(path), "%s/%s", STATS_ROOT, mode);
    FILE *f = fopen(path, "re");
    if (!f) return 0;
    while (fgets(line, sizeof(line), f)) {
        for (unsigned i = 0; i < 5; i++) {
            size_t len = strlen(labels[i]);
            if (!strncmp(line, labels[i], len) && !number(line + len, &values[i])) fields |= 1U << i;
        }
    }
    int bad = ferror(f);
    fclose(f);
    if (bad || (fields & 15) != 15) return 0;
    for (unsigned i = 0; i < 5; i++)
        if (fields & (1U << i))
            emit("kind=counter sample=%u source=qcom mode=%s key=%s value=%" PRIu64,
                 sample, mode, keys[i], values[i]);
    return 1;
}

// A BOOTTIME-MONOTONIC increase identifies time actually spent suspended.
// The success counter also catches short/coalesced cycles when available.
static int resumed(uint64_t gap, uint64_t previous_gap, int have_success,
                   uint64_t cycles, int had_success, uint64_t previous_cycles)
{
    return (gap > previous_gap && gap - previous_gap > UINT64_C(10000000)) ||
           (have_success && had_success && cycles > previous_cycles);
}

static void sample(unsigned index, const char *reason, uint64_t boot, uint64_t begin,
                   uint64_t gap, uint64_t previous_gap, uint64_t cycles)
{
    emit("kind=sample sample=%u observer=resume_timer reason=%s elapsed_s=%" PRIu64
         " boottime_ms=%" PRIu64 " suspended_ms=%" PRIu64 " resume_cycles=%" PRIu64,
         index, reason, boot / NS >= begin ? boot / NS - begin : 0, boot / 1000000,
         gap > previous_gap ? (gap - previous_gap) / 1000000 : 0, cycles);
    // RPM comes first, before any slow shell/awk wake-source inventory.
    unsigned sources = (unsigned)rpm(index, "vlow") + (unsigned)rpm(index, "vmin");
    emit("kind=availability sample=%u observer=resume_timer soc_sources=%u", index, sources);
}

int main(int argc, char **argv)
{
    uint64_t begin, end;
    if (argc != 4 || strlen(argv[1]) != 16 || strspn(argv[1], "0123456789abcdef") != 16 ||
        number(argv[2], &begin) || number(argv[3], &end) || end < begin || end - begin > 7200)
        return 2;
    session = argv[1];
    output = open(KMSG_PATH, O_WRONLY | O_APPEND | O_CLOEXEC);
    if (output < 0) return 1;
    uint64_t mono = ns(CLOCK_MONOTONIC), boot = ns(CLOCK_BOOTTIME);
    if (boot == UINT64_MAX || mono == UINT64_MAX || boot < mono || end > boot / NS + 7200) return 1;
    uint64_t gap = boot - mono, previous_gap = gap, cycles = 0, previous_cycles = 0;
    int have_success = !success(&cycles), had_success = have_success;
    previous_cycles = cycles;
    unsigned index = 1;
    sample(index, "baseline", boot, begin, gap, gap, 0);
    uint64_t last_sample = boot;
    while (!output_failed && boot / NS < end) {
        uint64_t target = boot + NS;
        if (target / NS >= end) target = end * NS;
        struct timespec until = {(time_t)(target / NS), (long)(target % NS)};
        int rc;
        // CLOCK_BOOTTIME, not CLOCK_BOOTTIME_ALARM: expiration never wakes
        // the phone, but an overdue wait returns as soon as this task thaws.
        do { rc = clock_nanosleep(CLOCK_BOOTTIME, TIMER_ABSTIME, &until, NULL); } while (rc == EINTR);
        if (rc) { output_failed = 1; break; }
        mono = ns(CLOCK_MONOTONIC); boot = ns(CLOCK_BOOTTIME);
        if (boot == UINT64_MAX || mono == UINT64_MAX || boot < mono) { output_failed = 1; break; }
        gap = boot - mono; have_success = !success(&cycles);
        int wake = resumed(gap, previous_gap, have_success, cycles, had_success, previous_cycles);
        if (wake || boot - last_sample >= 60 * NS || boot / NS >= end) {
            uint64_t delta = have_success && had_success && cycles >= previous_cycles ? cycles - previous_cycles : 0;
            sample(++index, wake ? "resume" : boot / NS >= end ? "final" : "periodic",
                   boot, begin, gap, previous_gap, delta);
            last_sample = boot;
        }
        previous_gap = gap; previous_cycles = cycles; had_success = have_success;
    }
    emit("kind=resume_monitor state=%s samples=%u", output_failed ? "failed" : "complete", index);
    close(output);
    printf("%u\n", index);
    return output_failed ? 1 : 0;
}
