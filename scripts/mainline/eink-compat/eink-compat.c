/* A9 v7 socket/property compatibility for the mainline E Ink driver.
 * No vendor blobs, DRM master, waveform generation or raw I2C access.
 * Contract and provenance: docs/mainline-eink-compat.md.
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <sys/system_properties.h>
#endif

#include "eink-policy.h"

#define CLIENTS 16
#define LINE 512
static const char *root = "", *props = NULL, *tools = "/system/bin";
static const char *socket_name = "a9_eink_socket";
static pthread_mutex_t prop_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t diag_lock = PTHREAD_MUTEX_INITIALIZER;
static int diag_left; /* opt-in, at most 4096 numeric records per process */
static int diag_enabled; /* immutable after startup, before either thread */

static int64_t monotonic_ms(void);

static void diagnostic(const char *event, unsigned sequence, int value, int accepted)
{
    if (!diag_enabled) return;
    pthread_mutex_lock(&diag_lock);
    if (diag_left > 0) {
        --diag_left;
        fprintf(stderr, "a9-eink-diag ms=%lld event=%s seq=%u value=%d accepted=%d\n",
                (long long)monotonic_ms(), event, sequence, value, accepted);
    }
    pthread_mutex_unlock(&diag_lock);
}

static int path_join(char *out, size_t size, const char *base, const char *tail)
{
    int n = snprintf(out, size, "%s%s", base, tail);
    if (n < 0 || (size_t)n >= size) { errno = ENAMETOOLONG; return -1; }
    return 0;
}

static int write_path(const char *path, const char *value, int fake_property)
{
    int flags = O_WRONLY | O_CLOEXEC;
    if (*root || fake_property) flags |= O_TRUNC;
    if (fake_property) flags |= O_CREAT;
    int fd = open(path, flags, 0600);
    if (fd < 0) return -1;
    size_t len = strlen(value);
    ssize_t n;
    do { n = write(fd, value, len); } while (n < 0 && errno == EINTR);
    int err = errno;
    int closed = close(fd);
    if (n != (ssize_t)len) { errno = n < 0 ? err : EIO; return -1; }
    return closed;
}

static int write_int_path(const char *path, int value)
{
    char text[32];
    snprintf(text, sizeof(text), "%d\n", value);
    int ret = write_path(path, text, 0);
    if (ret < 0) fprintf(stderr, "a9-eink-compat: %s: %s\n", path, strerror(errno));
    return ret;
}

static int sys_write(const char *suffix, int value)
{
    char path[PATH_MAX];
    if (path_join(path, sizeof(path), root, suffix) < 0) return -1;
    return write_int_path(path, value);
}

/* DRM card numbers depend on probe order (vkms is built in). Never pick an
 * arbitrary connector just because it has a directory called "eink". */
static int a9_driver_at(const char *device)
{
    const char *tails[] = {"/driver", "/device/driver"};
    for (size_t i = 0; i < sizeof(tails) / sizeof(tails[0]); i++) {
        char link[PATH_MAX], resolved[PATH_MAX];
        if (path_join(link, sizeof(link), device, tails[i]) < 0 || !realpath(link, resolved)) continue;
        const char *name = strrchr(resolved, '/');
        if (name && (!strcmp(name + 1, "a9-eink") || !strcmp(name + 1, "a9_eink"))) return 1;
    }
    return 0;
}

static int a9_group_owner(const char *group)
{
    char device[PATH_MAX];
    if (path_join(device, sizeof(device), group, "") < 0) return 0;
    /* Connector -> card -> platform device; also handle class-only fixtures
     * with card/device/driver. realpath() has already resolved class symlinks. */
    for (int depth = 0; depth < 6; depth++) {
        if (a9_driver_at(device)) return 1;
        char *slash = strrchr(device, '/');
        if (!slash || slash == device) break;
        *slash = 0;
    }
    return 0;
}

static int eink_group(char selected[PATH_MAX])
{
    const char *patterns[] = {
        "/sys/devices/platform/eink-display/drm/card[0-9]*/card*-*/eink",
        "/sys/class/drm/card*-*/eink",
    };
    const char *attributes[] = {"/mode", "/black_threshold", "/white_threshold", "/contrast", "/force_clear", "/manual_clear"};
    char sysroot[PATH_MAX], canonical_root[PATH_MAX];
    if (path_join(sysroot, sizeof(sysroot), root, "/sys") < 0 || !realpath(sysroot, canonical_root)) return -1;
    size_t root_len = strlen(canonical_root);
    selected[0] = 0;
    for (size_t i = 0; i < sizeof(patterns) / sizeof(patterns[0]); i++) {
        char pattern[PATH_MAX];
        if (path_join(pattern, sizeof(pattern), root, patterns[i]) < 0) return -1;
        glob_t matches = {0};
        int ret = glob(pattern, 0, NULL, &matches);
        if (ret && ret != GLOB_NOMATCH) { globfree(&matches); errno = EIO; return -1; }
        for (size_t j = 0; j < matches.gl_pathc; j++) {
            char candidate[PATH_MAX];
            if (!realpath(matches.gl_pathv[j], candidate)) continue;
            /* In host tests no discovered symlink may escape the fake sysfs. */
            if (strncmp(candidate, canonical_root, root_len) || candidate[root_len] != '/') continue;
            if (i != 0 && !a9_group_owner(candidate)) continue;
            int complete = 1;
            for (size_t k = 0; k < sizeof(attributes) / sizeof(attributes[0]); k++) {
                char file[PATH_MAX];
                struct stat st;
                if (path_join(file, sizeof(file), candidate, attributes[k]) < 0 ||
                    stat(file, &st) || !S_ISREG(st.st_mode)) { complete = 0; break; }
            }
            if (!complete) continue;
            if (*selected && strcmp(selected, candidate)) {
                globfree(&matches); errno = EEXIST; return -1;
            }
            strcpy(selected, candidate); /* realpath guarantees PATH_MAX bound */
        }
        globfree(&matches);
    }
    if (!*selected) { errno = ENODEV; return -1; }
    return 0;
}

static int epd_write(const char *attribute, int value)
{
    char group[PATH_MAX], path[PATH_MAX];
    /* Rediscover per request: late probe/removal/renumbering must never leave
     * a cached card index pointing at vkms or the transport's msm connector. */
    if (eink_group(group) < 0) {
        fprintf(stderr, "a9-eink-compat: E Ink group discovery: %s\n", strerror(errno));
        return -1;
    }
    if (snprintf(path, sizeof(path), "%s/%s", group, attribute) >= (int)sizeof(path)) {
        errno = ENAMETOOLONG; return -1;
    }
    return write_int_path(path, value);
}

static int prop_get(const char *key, char *value, size_t size)
{
    if (props) {
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s/%s", props, key) >= (int)sizeof(path)) return -1;
        pthread_mutex_lock(&prop_lock);
        FILE *f = fopen(path, "r");
        int ok = f && fgets(value, (int)size, f);
        if (f) fclose(f);
        pthread_mutex_unlock(&prop_lock);
        if (!ok) { value[0] = 0; return -1; }
        value[strcspn(value, "\r\n")] = 0;
        return 0;
    }
#ifdef __ANDROID__
    char buf[PROP_VALUE_MAX];
    if (__system_property_get(key, buf) <= 0) { value[0] = 0; return -1; }
    if (strlen(buf) >= size) return -1;
    strcpy(value, buf);
    return 0;
#else
    (void)key;
    value[0] = 0;
    return -1;
#endif
}

static int prop_set(const char *key, const char *value)
{
    if (props) {
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s/%s", props, key) >= (int)sizeof(path)) return -1;
        pthread_mutex_lock(&prop_lock);
        int ret = write_path(path, value, 1);
        pthread_mutex_unlock(&prop_lock);
        return ret;
    }
#ifdef __ANDROID__
    return __system_property_set(key, value);
#else
    (void)key; (void)value;
    return -1;
#endif
}

static int number(const char *s, int max, int *value)
{
    size_t n = strlen(s);
    if (!n || n > 4) return -1;
    int v = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;
        v = 10 * v + s[i] - '0';
    }
    if (v > max) return -1;
    *value = v;
    return 0;
}

struct lamp { const char *prop, *path; int gain, input_max, last; const char *option; };
static struct lamp lamps[] = {
    {"sys.linevibrator_open", "/sys/class/leds/a9:warm:center/brightness", 81, 2200, -1, "--led-warm-center"},
    {"sys.linevibrator_short", "/sys/class/leds/a9:white:center/brightness", 75, 2200, -1, "--led-white-center"},
    {"sys.linevibrator_open", "/sys/class/leds/a9:warm:edge/brightness", 78, 2047, -1, "--led-warm-edge"},
    {"sys.linevibrator_short", "/sys/class/leds/a9:white:edge/brightness", 72, 2047, -1, "--led-white-edge"},
};

/* Return true if a failed write needs retry after driver probe. Cache only
 * successful outputs, independently for each chip. Never synthesize a default. */
static int sync_lamps(void)
{
    int retry = 0;
    for (size_t i = 0; i < sizeof(lamps) / sizeof(lamps[0]); i++) {
        struct lamp *l = &lamps[i];
        char text[128];
        int value;
        if (prop_get(l->prop, text, sizeof(text)) || number(text, 2200, &value)) continue;
        if (value > l->input_max) value = l->input_max;
        value = value * l->gain / 100;
        if (value == l->last) continue;
        if (sys_write(l->path, value) == 0) {
            l->last = value;
            diagnostic("lamp", (unsigned)i, value, 1);
        }
        else retry = 1;
    }
    return retry;
}

static void *property_loop(void *unused)
{
    (void)unused;
#ifdef __ANDROID__
    uint32_t serial = 0;
#endif
    for (;;) {
        int retry = sync_lamps();
#ifdef __ANDROID__
        if (!props) {
            struct timespec timeout = {.tv_sec = 1, .tv_nsec = 0};
            /* NULL observes the global property serial, including late creation.
             * The serial is sampled before sync by the previous wait, so writes
             * during sync cannot be lost. No periodic wakeups when healthy. */
            __system_property_wait(NULL, serial, &serial, retry ? &timeout : NULL);
            continue;
        }
#endif
        (void)retry;
        struct timespec pause = {.tv_sec = 0, .tv_nsec = 50000000};
        nanosleep(&pause, NULL);
    }
    return NULL;
}

static int64_t monotonic_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* Only the socket event loop owns these controls. A request can arrive before
 * the DRM connector exists; retain its latest value until a write succeeds.
 * Do not turn linevibrator shader/haptic/frontlight props into refresh modes,
 * or repeatedly overwrite successfully applied settings. Kernel probe owns
 * the Smooth/518 and 30/30/0 boot defaults. Clear is an event, never replayed.
 */
enum epd_control { MODE, BLACK, WHITE, CONTRAST, CLEAR_SHADOW };
static unsigned mode_sequence;
static struct {
    const char *attribute;
    int value, pending;
    int64_t retry_at;
} controls[] = {
    {"mode", 0, 0, 0}, {"black_threshold", 0, 0, 0},
    {"white_threshold", 0, 0, 0}, {"contrast", 0, 0, 0},
    {"clear_shadow", 0, 0, 0},
};
/* clear_shadow is an optional kernel attribute: give up after a bounded number
 * of failed writes instead of retrying forever on an older kernel. */
static int shadow_tries;

static struct ep_dyn dyn;
static struct ep_ac act;
static struct ep_gate clear_gate; /* global automatic clear limiter */

static void apply_control(enum epd_control control)
{
    if (!epd_write(controls[control].attribute, controls[control].value)) {
        controls[control].pending = 0;
        if (control == MODE)
            diagnostic("mode", mode_sequence, controls[control].value, 1);
    } else if (control == CLEAR_SHADOW && ++shadow_tries >= 20) {
        controls[control].pending = 0;
        fprintf(stderr, "a9-eink-compat: clear_shadow not accepted by the kernel; giving up\n");
    } else
        controls[control].retry_at = monotonic_ms() + 1000;
}

static int set_control(enum epd_control control, int value)
{
    if (control == MODE) ++mode_sequence;
    controls[control].value = value;
    controls[control].pending = 1;
    apply_control(control);
    if (control == MODE && controls[control].pending)
        diagnostic("mode", mode_sequence, value, 0);
    if (controls[control].pending)
        fprintf(stderr, "a9-eink-compat: deferred %s=%d until sysfs accepts it\n",
                controls[control].attribute, value);
    return 0; /* This write-only protocol acknowledges no physical outcome. */
}

/* Optional mainline-only boot default: ro.vendor.a9ml.eink.default_mode names
 * a refresh mode (b/c/s/p or 513/515/518/521). Absent or invalid = nothing
 * written (kernel keeps Smooth/518). It goes through the same deferred-retry
 * path as a socket request, so a late probe is handled; any later app request
 * simply replaces it. Never touches thresholds, clear or power.
 */
static int mode_locked;
/* ro.vendor.a9ml.eink.lock_mode=1 (with a valid default_mode): app mode
 * requests are acknowledged but ignored, so a controlled comparison cannot be
 * flipped back to Smooth by the app's own startup applyMode(). Thresholds and
 * clear stay live. Off by default. */
static int mode_request(int value)
{
    if (mode_locked) {
        fprintf(stderr, "a9-eink-compat: mode %d request ignored (lock_mode)\n", value);
        return 0;
    }
    ep_dyn_user_mode(&dyn, value);
    return set_control(MODE, value);
}

static void apply_default_mode(void)
{
    char text[16];
    int value = 0;
    if (prop_get("ro.vendor.a9ml.eink.default_mode", text, sizeof(text))) return;
    if (!strcmp(text, "b") || !strcmp(text, "513")) value = 513;
    else if (!strcmp(text, "c") || !strcmp(text, "515")) value = 515;
    else if (!strcmp(text, "s") || !strcmp(text, "518")) value = 518;
    else if (!strcmp(text, "p") || !strcmp(text, "521")) value = 521;
    else { fprintf(stderr, "a9-eink-compat: ignoring bad default_mode '%s'\n", text); return; }
    ep_dyn_user_mode(&dyn, value);
    set_control(MODE, value);
    if (!prop_get("ro.vendor.a9ml.eink.lock_mode", text, sizeof(text)) && !strcmp(text, "1"))
        mode_locked = 1;
}

static int prop_number(const char *key, int max, int dflt)
{
    char text[16];
    int v;
    if (prop_get(key, text, sizeof(text)) || number(text, max, &v)) return dflt;
    return v;
}

/* All optional and off by default (mainline image only):
 *   ro.vendor.a9ml.eink.clear_shadow=0|1   Clear UI shadow curve (kernel attribute)
 *   ro.vendor.a9ml.eink.dyn_refresh=1      scroll-time mode switch (needs "sv" events)
 *     .dyn_fast=b|s (b)  .dyn_events=N (4)  .dyn_window_ms (500)  .dyn_restore_ms (1000)
 *     .dyn_any_rest=1    also switch when the rest mode is not Clear (stock: Clear only)
 *   ro.vendor.a9ml.eink.activity_clear=1   clear on activity change (needs "ac" events)
 *     .activity_delay_ms (300)  .activity_rearm_ms (6000)
 * The rest mode is the default_mode if valid, else assumed to be the kernel's 518. */
static void apply_policy_props(void)
{
    char text[16];
    int fast = 513;
    if (!prop_get("ro.vendor.a9ml.eink.dyn_fast", text, sizeof(text)) && (!strcmp(text, "s") || !strcmp(text, "518")))
        fast = 518;
    int rest = dyn.rest ? dyn.rest : 518;
    ep_dyn_init(&dyn, prop_number("ro.vendor.a9ml.eink.dyn_refresh", 1, 0), rest, fast,
                (unsigned)prop_number("ro.vendor.a9ml.eink.dyn_events", 100, 4),
                prop_number("ro.vendor.a9ml.eink.dyn_window_ms", 5000, 500),
                prop_number("ro.vendor.a9ml.eink.dyn_restore_ms", 5000, 1000),
                !prop_number("ro.vendor.a9ml.eink.dyn_any_rest", 1, 0));
    ep_ac_init(&act, prop_number("ro.vendor.a9ml.eink.activity_clear", 1, 0),
               prop_number("ro.vendor.a9ml.eink.activity_delay_ms", 5000, 300),
               prop_number("ro.vendor.a9ml.eink.activity_rearm_ms", 9999, 6000));
    int shadow = prop_number("ro.vendor.a9ml.eink.clear_shadow", 1, -1);
    if (shadow >= 0) set_control(CLEAR_SHADOW, shadow);
}

static int min_timeout(int a, int64_t b)
{
    if (b < 0) return a;
    if (b > 1000000) b = 1000000;
    return a < 0 || (int)b < a ? (int)b : a;
}

static void run_policy(void)
{
    int64_t now = monotonic_ms();
    if (ep_dyn_tick(&dyn, now) == EP_SET_REST) set_control(MODE, dyn.rest);
    if (ep_ac_tick(&act, now)) {
        if (ep_gate_auto_ok(&clear_gate, now)) {
            ep_gate_note(&clear_gate, now);	/* attempts count, a failed write is not retried */
            epd_write("manual_clear", 1);
        } else
            fprintf(stderr, "a9-eink-compat: automatic clear dropped (global rate limit)\n");
    }
}

static int retry_controls(void)
{
    int timeout = -1;
    for (size_t i = 0; i < sizeof(controls) / sizeof(controls[0]); i++) {
        if (!controls[i].pending) continue;
        if (monotonic_ms() >= controls[i].retry_at)
            apply_control((enum epd_control)i);
        if (!controls[i].pending) continue;
        int64_t delay = controls[i].retry_at - monotonic_ms();
        int ms = delay > 0 ? (int)delay : 0;
        if (timeout < 0 || ms < timeout) timeout = ms;
    }
    return timeout;
}

/* Fixed argv only; no shell. Capture and validate Binder status, bound a child
 * to 550 ms so two warmth calls fit the app's 1500 ms reply timeout. */
static int run_tool(const char *tool, char *const argv[], int parcel)
{
    char path[PATH_MAX], output[4096];
    if (snprintf(path, sizeof(path), "%s/%s", tools, tool) >= (int)sizeof(path)) return 0;
    int p[2];
    if (pipe2(p, O_CLOEXEC | O_NONBLOCK)) return 0;
    pid_t pid = fork();
    if (pid == 0) {
        if (setpgid(0, 0) < 0) _exit(127);
        close(p[0]);
        /* exec inherits a blocking stdout, as system tools expect. */
        fcntl(p[1], F_SETFL, 0);
        if (dup2(p[1], STDOUT_FILENO) < 0) _exit(127);
        close(p[1]);
        execv(path, argv);
        _exit(127);
    }
    close(p[1]);
    if (pid < 0) { close(p[0]); return 0; }
    /* Also stop descendants if a system wrapper forks a Binder client. */
    (void)setpgid(pid, pid);
    size_t used = 0;
    int status = 0, done = 0, timed_out = 0;
    int64_t deadline = monotonic_ms() + 550;
    for (;;) {
        char scratch[512];
        ssize_t n;
        while ((n = read(p[0], scratch, sizeof(scratch))) > 0) {
            size_t take = (size_t)n;
            if (take > sizeof(output) - used - 1) take = sizeof(output) - used - 1;
            memcpy(output + used, scratch, take);
            used += take;
            if (monotonic_ms() >= deadline) break;
        }
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid) { done = 1; break; }
        if (w < 0 && errno != EINTR) break;
        if (monotonic_ms() >= deadline) { timed_out = 1; break; }
        struct pollfd fd = {.fd = p[0], .events = POLLIN};
        poll(&fd, 1, 5);
    }
    if (!done) {
        kill(-pid, SIGKILL);
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    }
    /* Drain after exit as well: the child can exit between read and waitpid. */
    ssize_t n;
    while (used < sizeof(output) - 1 && (n = read(p[0], output + used, sizeof(output) - used - 1)) > 0)
        used += (size_t)n;
    close(p[0]);
    output[used] = 0;
    if (!done || timed_out || !WIFEXITED(status) || WEXITSTATUS(status)) return 0;
    unsigned exception = 1, result = 0;
    return !parcel || (sscanf(output, "Result: Parcel( %x %x", &exception, &result) == 2 &&
                       exception == 0 && result == 1);
}

static int night_display(const char *temperature)
{
    char sdk[128];
    int value;
    if (number(temperature, 4082, &value) || value < 2596 ||
        prop_get("ro.build.version.sdk", sdk, sizeof(sdk)) || strcmp(sdk, "36")) return 0;
    char *activate[] = {"service", "call", "color_display", "7", "i32", "1", NULL};
    char *set[] = {"service", "call", "color_display", "9", "i32", (char *)temperature, NULL};
    return run_tool("service", activate, 1) && run_tool("service", set, 1);
}

static int theme(const char *s)
{
    static const char *styles[] = {"TONAL_SPOT", "VIBRANT", "RAINBOW", "EXPRESSIVE", "FRUIT_SALAD", "SPRITZ"};
    char color[7], *end;
    errno = 0;
    long index = strtol(s, &end, 10);
    if (end == s || errno || !isspace((unsigned char)*end)) return -1;
    while (isspace((unsigned char)*end)) end++;
    if (strlen(end) != 6) return -1;
    memcpy(color, end, sizeof(color));
    for (size_t i = 0; i < 6; i++) if (!isxdigit((unsigned char)color[i])) return -1;
    if (index < 0 || index > 5) index = 5;
    char json[320];
    snprintf(json, sizeof(json), "{\"android.theme.customization.theme_style\":\"%s\","
             "\"android.theme.customization.color_source\":\"preset\","
             "\"android.theme.customization.system_palette\":\"%s\"}", styles[index], color);
    char *args[] = {"settings", "put", "secure", "theme_customization_overlay_packages", json, NULL};
    return run_tool("settings", args, 0) ? 0 : -1;
}

/* All commands except sct are write-only, exactly as the shipped app expects.
 * In particular a failed r must NOT leave a reply ahead of the next sct reply. */
static int command(const char *s, int client)
{
    if (!strncmp(s, "sct", 3)) {
        int ok = night_display(s + 3);
        if (send(client, ok ? "OK\n" : "NO\n", 3, MSG_NOSIGNAL) != 3) return -1;
        return ok ? 0 : -1;
    }
    /* Manual refresh is an event: no retry and no GC16 userspace fallback. */
    if (!strcmp(s, "r")) { ep_gate_note(&clear_gate, monotonic_ms()); return epd_write("manual_clear", 1); }
    if (!strcmp(s, "sv")) { /* one vertical scroll event */
        if (ep_dyn_event(&dyn, monotonic_ms()) == EP_SET_FAST) set_control(MODE, dyn.fast);
        return 0;
    }
    if (!strcmp(s, "ac")) { ep_ac_event(&act, monotonic_ms()); return 0; }
    if (!strcmp(s, "shadow0") || !strcmp(s, "shadow1")) {
        shadow_tries = 0;
        return set_control(CLEAR_SHADOW, s[6] - '0');
    }
    if (!strcmp(s, "cm")) { errno = EOPNOTSUPP; return -1; } /* No bitmap was ever uploaded on v7. */
    if (!strcmp(s, "b")) return mode_request(513);
    if (!strcmp(s, "c")) return mode_request(515);
    if (!strcmp(s, "s")) return mode_request(518);
    if (!strcmp(s, "p")) return mode_request(521);
    if (!strncmp(s, "theme", 5)) return theme(s + 5);
    struct { const char *prefix; enum epd_control control; int max; } knobs[] = {
        {"stb", BLACK, 100}, {"stw", WHITE, 100}, {"sco", CONTRAST, 100},
    };
    int value;
    for (size_t i = 0; i < sizeof(knobs) / sizeof(knobs[0]); i++)
        if (!strncmp(s, knobs[i].prefix, 3)) {
            if (number(s + 3, knobs[i].max, &value)) return -1;
            return set_control(knobs[i].control, value);
        }
    struct { const char *prefix, *key; int max, min; } keys[] = {
        {"stl", "sys.linevibrator_type", 97, 0},
        {"smb", "sys.linevibrator_touch", 2200, 1},
        {"wov", "sys.wakeup_on_volume", 1, 0},
    };
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
        if (!strncmp(s, keys[i].prefix, 3)) {
            if (number(s + 3, keys[i].max, &value) || value < keys[i].min) return -1;
            return prop_set(keys[i].key, s + 3);
        }
    errno = EINVAL;
    return -1;
}

struct client { int fd; size_t used; int discard; char line[LINE]; };

static int serve(void)
{
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    size_t len = strlen(socket_name);
    if (!len || len > sizeof(addr.sun_path) - 1) return 1;
    memcpy(addr.sun_path + 1, socket_name, len);
    int server = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (server < 0) { perror("socket"); return 1; }
    socklen_t addrlen = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + len);
    if (bind(server, (struct sockaddr *)&addr, addrlen) || listen(server, CLIENTS)) {
        perror("bind/listen (init must finish stopping the stock socket owner)"); close(server); return 1;
    }
    pthread_t worker;
    int err = pthread_create(&worker, NULL, property_loop, NULL);
    if (err) { fprintf(stderr, "pthread_create: %s\n", strerror(err)); close(server); return 1; }
    struct client clients[CLIENTS];
    for (int i = 0; i < CLIENTS; i++) clients[i] = (struct client){.fd = -1};
    apply_default_mode();
    apply_policy_props();
    ep_gate_init(&clear_gate, monotonic_ms());
    for (;;) {
        run_policy();
        int timeout = retry_controls();
        timeout = min_timeout(timeout, ep_dyn_timeout(&dyn, monotonic_ms()));
        timeout = min_timeout(timeout, ep_ac_timeout(&act, monotonic_ms()));
        struct pollfd fds[CLIENTS + 1] = {{.fd = server, .events = POLLIN}};
        for (int i = 0; i < CLIENTS; i++) fds[i + 1] = (struct pollfd){.fd = clients[i].fd, .events = POLLIN};
        if (poll(fds, CLIENTS + 1, timeout) < 0) { if (errno == EINTR) continue; perror("poll"); return 1; }
        if (fds[0].revents & POLLIN) {
            int fd = accept4(server, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
            if (fd >= 0) {
                struct ucred cred;
                socklen_t size = sizeof(cred);
                int slot;
                for (slot = 0; slot < CLIENTS && clients[slot].fd >= 0; slot++) {}
                /* SELinux is the app identity gate. UID is a second coarse
                 * check, because the platform-signed app has a dynamic UID. */
                if (slot == CLIENTS || getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &size) ||
                    !(cred.uid == 0 || cred.uid == 1000 || (props && cred.uid == geteuid()) ||
                      (cred.uid % 100000 >= 10000 && cred.uid % 100000 < 99000))) close(fd);
                else clients[slot] = (struct client){.fd = fd};
            }
        }
        for (int i = 0; i < CLIENTS; i++) {
            struct client *c = &clients[i];
            if (c->fd < 0 || !fds[i + 1].revents) continue;
            char data[1024];
            ssize_t n = read(c->fd, data, sizeof(data));
            if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (n <= 0) { close(c->fd); c->fd = -1; continue; }
            for (ssize_t j = 0; j < n; j++) {
                unsigned char b = (unsigned char)data[j];
                if (b == '\n') {
                    if (!c->discard && c->used) {
                        c->line[c->used] = 0;
                        if (command(c->line, c->fd) < 0)
                            fprintf(stderr, "a9-eink-compat: rejected/failed command %.32s\n", c->line);
                    }
                    c->used = 0; c->discard = 0;
                } else if (b < 32 || b > 126 || c->used >= LINE - 1) {
                    c->discard = 1;
                } else if (!c->discard) c->line[c->used++] = (char)b;
            }
        }
    }
}

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        if (i + 1 >= argc) goto usage;
        if (!strcmp(argv[i], "--root")) root = argv[++i];
        else if (!strcmp(argv[i], "--props")) props = argv[++i];
        else if (!strcmp(argv[i], "--socket")) socket_name = argv[++i];
        else if (!strcmp(argv[i], "--tools")) tools = argv[++i];
        else if (!strcmp(argv[i], "--diagnostics")) {
            if (number(argv[++i], 4096, &diag_left)) goto usage;
            diag_enabled = diag_left > 0;
        }
        else {
            size_t led;
            for (led = 0; led < sizeof(lamps) / sizeof(lamps[0]); led++)
                if (!strcmp(argv[i], lamps[led].option)) break;
            if (led == sizeof(lamps) / sizeof(lamps[0]) || strncmp(argv[i + 1], "/sys/", 5)) goto usage;
            lamps[led].path = argv[++i];
        }
    }
    /* Host execution is possible only against explicitly supplied fake trees. */
#ifndef __ANDROID__
    if (!*root || !props || !strcmp(tools, "/system/bin")) goto usage;
#endif
    signal(SIGPIPE, SIG_IGN);
    return serve();
usage:
    fprintf(stderr, "usage: %s [--root FAKE_ROOT --props PROPERTY_DIR --tools FAKE_TOOLS --socket NAME] [--diagnostics 0..4096]\n"
            "  [--led-warm-center /sys/.../brightness] [--led-white-center /sys/.../brightness]\n"
            "  [--led-warm-edge /sys/.../brightness] [--led-white-edge /sys/.../brightness]\n", argv[0]);
    return 2;
}
