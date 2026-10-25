// SPDX-License-Identifier: GPL-2.0-only
/* Tinyalsa adapter for the unmodified AOSP primary AIDL HAL. Only this HAL
 * preloads it. CARD_0_DEV_24 is an earpiece alias of actual MM1 (PCM0).
 * The backend must be selected before pcm_open: DPCM rejects unrouted opens.
 * No process spawning, register writes, or polling. Bluetooth bypasses this.
 */
#define _GNU_SOURCE
#define A9_AUDIO_TEST
#include "../a9-audio-ctl.c"
#include <dlfcn.h>
#include <pthread.h>
#include <android/log.h>
#include "routes.h"
#define A9_QUIET_LOG(...) __android_log_print(ANDROID_LOG_ERROR, "A9AudioRoute", __VA_ARGS__)
#include "../a9-audio-quiet.h"
struct pcm;
struct pcm_config;
struct pcm_params;
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define PCM_IN 0x10000000u
static pthread_mutex_t route_lock = PTHREAD_MUTEX_INITIALIZER;
static struct pcm *active;
static struct pcm *capture_active;
static int capture_read_reported;
static unsigned active_device;
static int write_error_reported;
static struct pcm *(*next_open)(unsigned, unsigned, unsigned, const struct pcm_config *);
static int (*next_close)(struct pcm *);
static int (*next_ready)(const struct pcm *);
static struct pcm_params *(*next_params)(unsigned, unsigned, unsigned);
static int (*next_write)(struct pcm *, const void *, unsigned);
static int (*next_read)(struct pcm *, void *, unsigned);
static const char *(*next_error)(const struct pcm *);
static int (*next_pcm_fd)(const struct pcm *);
static pthread_once_t once = PTHREAD_ONCE_INIT;
static void resolve(void) {
    next_open = dlsym(RTLD_NEXT, "pcm_open");
    next_close = dlsym(RTLD_NEXT, "pcm_close");
    next_ready = dlsym(RTLD_NEXT, "pcm_is_ready");
    next_params = dlsym(RTLD_NEXT, "pcm_params_get");
    next_write = dlsym(RTLD_NEXT, "pcm_write");
    next_read = dlsym(RTLD_NEXT, "pcm_read");
    next_error = dlsym(RTLD_NEXT, "pcm_get_error");
    next_pcm_fd = dlsym(RTLD_NEXT, "pcm_get_file_descriptor");
}
static int apply(int fd, const struct route_ctl *items, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        struct snd_ctl_elem_info info;
        if (lookup(fd, items[i].name, &info) || write_verified(fd, &info, items[i].value)) {
            __android_log_print(ANDROID_LOG_ERROR, "A9AudioRoute", "control %s=%s failed: %s",
                                items[i].name, items[i].value, strerror(errno));
            return -1;
        }
    }
    return 0;
}
static int control(void) {
    int fd = open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC);
    struct snd_ctl_card_info ci = {0};
    if (fd < 0) return -1;
    if (ioctl(fd, SNDRV_CTL_IOCTL_CARD_INFO, &ci) ||
        strncmp((char *)ci.name, "Hisense-A9-Audio", sizeof(ci.name))) {
        close(fd); errno = ENODEV; return -1;
    }
    return fd;
}
static int off(int fd) {
    int rc = 0;
    /* Try every endpoint even if one failed, so a partially applied route
     * doesn't leave another endpoint powered. */
    for (size_t i = 0; i < COUNT(route_off); ++i)
        if (apply(fd, &route_off[i], 1)) rc = -1;
    return rc;
}
static int select_route(int fd, unsigned device, int capture, int opening) {
    if (capture) return apply(fd, route_main_mic, COUNT(route_main_mic));
    if (off(fd)) return -1;
    if (device == 24) return apply(fd, route_earpiece, COUNT(route_earpiece));
    if (device == 25) return apply(fd, route_headphone, COUNT(route_headphone));
    for (size_t i = 0; i < COUNT(route_speaker); ++i) {
        /* A capabilities query must not select a stock Music scene. Only
         * actual output opens enable the amplifier before opening PCM. */
        if (!opening && !strcmp(route_speaker[i].name, "aw87xxx_mode_switch_a")) continue;
        if (apply(fd, &route_speaker[i], 1)) return -1;
    }
    return 0;
}
static int output(unsigned card, unsigned device, unsigned flags) {
    return card == 0 && !(flags & PCM_IN) && (device == 0 || device == 24 || device == 25);
}
struct pcm *pcm_open(unsigned card, unsigned device, unsigned flags, const struct pcm_config *config) {
    pthread_once(&once, resolve);
    if (!next_open || !next_close || !next_ready) { errno = ENOSYS; return NULL; }
    /* The GSI recursively tries SCO variants when all are absent. Declare
     * their policy ports, but reject their reserved addresses: no SCO PCM
     * transport exists yet. Never accidentally play call audio on speaker. */
    if (card == 0 && (device == 30 || device == 31)) { errno = ENOTSUP; return NULL; }
    unsigned requested_device = device;
    /* StreamPrimary defaults to CARD_0_DEV_0 when an attached device's id
     * address is empty. MM1 has no capture frontend on this board: the only
     * built-in input is 48 kHz mono S16 MM2. Keep SCO's 8/16 kHz requests
     * rejected instead of sending them to the built-in microphone. */
    if (card == 0 && device == 0 && (flags & PCM_IN)) {
        unsigned fields[5] = {0};
        if (config) memcpy(fields, config, sizeof(fields));
        if (fields[0] != 1 || fields[1] != 48000 || fields[4] != 0) {
            errno = ENOTSUP; return NULL;
        }
        device = 1;
    }
    int is_output = output(card, device, flags);
    int capture = card == 0 && device == 1 && (flags & PCM_IN);
    if (!is_output && !capture) return next_open(card, device, flags, config);
    pthread_mutex_lock(&route_lock);
    if (is_output && active) { pthread_mutex_unlock(&route_lock); errno = EBUSY; return NULL; }
    int fd = control();
    if (fd < 0) { pthread_mutex_unlock(&route_lock); return NULL; }
    if (select_route(fd, device, capture, 1)) {
        if (is_output) off(fd);
        close(fd); pthread_mutex_unlock(&route_lock); errno = EIO; return NULL;
    }
    struct pcm *p = next_open(card, is_output ? 0 : device, flags, config);
    if (capture && p && next_ready(p)) { capture_active = p; capture_read_reported = 0; }
    if (is_output) {
        if (p && next_ready(p)) { active = p; active_device = device; write_error_reported = 0; }
        else off(fd);
    }
    close(fd);
    __android_log_print(ANDROID_LOG_INFO, "A9AudioRoute", "MM%s %s route=%u -> PCM%u ready=%d",
                        capture ? "2" : "1", capture ? "capture" : "playback", requested_device,
                        is_output ? 0 : device, p && next_ready(p));
    pthread_mutex_unlock(&route_lock);
    return p;
}
/* Keep the first capture boundary even if the donor blocks inside pcm_read.
 * No samples or device identifiers are logged, and read/errno stay intact. */
int pcm_read(struct pcm *p, void *data, unsigned bytes) {
    pthread_once(&once, resolve);
    if (!next_read) { errno = ENOSYS; return -1; }
    pthread_mutex_lock(&route_lock);
    int first = p && p == capture_active && !capture_read_reported;
    if (first) capture_read_reported = 1;
    pthread_mutex_unlock(&route_lock);
    if (first) __android_log_print(ANDROID_LOG_INFO, "A9AudioRoute", "MM2 capture read enter bytes=%u", bytes);
    int rc = next_read(p, data, bytes), saved = errno;
    if (first) __android_log_print(ANDROID_LOG_INFO, "A9AudioRoute", "MM2 capture read return rc=%d errno=%d", rc, saved);
    errno = saved;
    return rc;
}
/* The stock AOSP proxy exposes only pcm_write's -1 to StreamAlsa. Preserve
 * that API and errno, but retain the underlying failure once per MM1 open.
 * No retry, prepare, route, gain, sample or stream-state change here. */
int pcm_write(struct pcm *p, const void *data, unsigned bytes) {
    pthread_once(&once, resolve);
    if (!next_write) { errno = ENOSYS; return -1; }
    int rc = next_write(p, data, bytes), saved = errno;
    if (rc < 0) {
        pthread_mutex_lock(&route_lock);
        if (p && p == active && !write_error_reported) {
            struct snd_pcm_status status = {0};
            int fd = next_pcm_fd ? next_pcm_fd(p) : -1;
            int status_rc = fd >= 0 ? ioctl(fd, SNDRV_PCM_IOCTL_STATUS, &status) : -1;
            write_error_reported = 1;
            __android_log_print(ANDROID_LOG_ERROR, "A9AudioRoute",
                "MM1 write failed route=%u bytes=%u rc=%d errno=%d (%s) tinyalsa=%s status_rc=%d state=%d hw=%llu appl=%llu avail=%llu",
                active_device, bytes, rc, saved, strerror(saved),
                next_error ? next_error(p) : "unavailable", status_rc,
                status_rc ? -1 : (int)status.state,
                (unsigned long long)status.hw_ptr, (unsigned long long)status.appl_ptr,
                (unsigned long long)status.avail);
        }
        pthread_mutex_unlock(&route_lock);
    }
    errno = saved;
    return rc;
}
int pcm_close(struct pcm *p) {
    pthread_once(&once, resolve);
    if (!next_close) { errno = ENOSYS; return -1; }
    pthread_mutex_lock(&route_lock);
    if (p && p == active) {
        int fd = control();
        if (fd >= 0) { a9_quiet_output(fd, active_device); close(fd); }
    }
    int rc = next_close(p);
    if (p && p == capture_active) capture_active = NULL;
    if (p && p == active) {
        int fd = control();
        if (fd >= 0) { off(fd); close(fd); }
        __android_log_print(ANDROID_LOG_INFO, "A9AudioRoute", "route=%u closed", active_device);
        active = NULL;
    }
    pthread_mutex_unlock(&route_lock);
    return rc;
}
struct pcm_params *pcm_params_get(unsigned card, unsigned device, unsigned flags) {
    pthread_once(&once, resolve);
    if (!next_params) { errno = ENOSYS; return NULL; }
    if (card == 0 && device == 0 && (flags & PCM_IN)) device = 1;
    if (!output(card, device, flags)) return next_params(card, device, flags);
    pthread_mutex_lock(&route_lock);
    if (active && device != active_device) {
        pthread_mutex_unlock(&route_lock); errno = EBUSY; return NULL;
    }
    int fd = control();
    struct pcm_params *p = NULL;
    if (fd >= 0) {
        if (active || !select_route(fd, device, 0, 0)) p = next_params(card, 0, flags);
        if (!active) off(fd);
        close(fd);
    }
    pthread_mutex_unlock(&route_lock);
    return p;
}
