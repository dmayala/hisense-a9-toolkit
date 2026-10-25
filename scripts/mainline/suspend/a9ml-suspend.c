// SPDX-License-Identifier: GPL-2.0-only
// Bounded A9 s2idle POC. Run on the phone only by the coordinator.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <linux/rtc.h>
#include <linux/watchdog.h>
#include <poll.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>

// Qualcomm KPSS is clocked at 32764 Hz with a 20-bit counter: max 32 s.
// RTC +10 leaves 20 s to finish resume and prove adb through a host ACK.
#define ACK "/data/local/tmp/a9-pm-ack"
static int wd = -1, rtc = -1, kmsg = -1;
static bool restart_watchdogd;
static bool alarm_owned;
static struct rtc_wkalrm saved_alarm;
static bool pmtest_owned;
#define PMTEST_GO "/data/local/tmp/a9-pmtest-go"
#define PMTEST_ACK "/data/local/tmp/a9-pmtest-ack"

// Keep the existing stdout protocol and mirror each stage into ramoops' live
// console ring. Kernel timestamps also place these markers among PM callbacks.
static void progress(const char *format, ...)
{
    int saved = errno;
    char message[512];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    puts(message);
    if (kmsg >= 0 && dprintf(kmsg, "<5>a9ml-suspend: %s\n", message) < 0)
        fputs("PM warning=kmsg-write-failed\n", stderr);
    errno = saved;
}

static int put(const char *path, const char *value)
{
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    size_t size = strlen(value);
    int rc = write(fd, value, size) == (ssize_t)size ? 0 : -1;
    int err = errno;
    close(fd);
    errno = err;
    return rc;
}

static int get(const char *path, char *buf, size_t cap)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, cap - 1);
    close(fd);
    if (n < 0) return -1;
    buf[n] = 0;
    buf[strcspn(buf, "\n")] = 0;
    return 0;
}

static double now(clockid_t clock)
{
    struct timespec t;
    if (clock_gettime(clock, &t)) exit(1);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int service_wait(const char *wanted)
{
    char state[PROP_VALUE_MAX];
    // Wait before arming; this bounded handoff does not consume WDT time.
    for (int i = 0; i < 60; ++i) {
        __system_property_get("init.svc.watchdogd-gsi", state);
        if (!strcmp(state, wanted)) return 0;
        usleep(50000);
    }
    errno = ETIMEDOUT;
    return -1;
}

static int watchdogd_stop(void)
{
    char state[PROP_VALUE_MAX];
    __system_property_get("init.svc.watchdogd-gsi", state);
    if (!state[0] || !strcmp(state, "stopped")) {
        progress("PM watchdogd-gsi=already-stopped-or-absent");
        return 0;
    }
    // Restore only a service that was active before this invocation. Mark the
    // obligation before requesting stop, including a delayed init response.
    restart_watchdogd = true;
    progress("PM watchdogd-gsi=stop-request");
    if (__system_property_set("ctl.stop", "watchdogd-gsi") || service_wait("stopped")) return -1;
    progress("PM watchdogd-gsi=stopped");
    return 0;
}

static int watchdogd_restore(void)
{
    if (!restart_watchdogd) return 0;
    progress("PM watchdogd-gsi=start-request");
    if (__system_property_set("ctl.start", "watchdogd-gsi") || service_wait("running")) return -1;
    restart_watchdogd = false;
    progress("PM watchdogd-gsi=running");
    return 0;
}

static int watchdog_arm(int seconds)
{
    glob_t nodes = {0};
    char buf[256], node[64];
    if (get("/sys/module/qcom_wdt/parameters/a9_guard", buf, sizeof(buf)) ||
        strcmp(buf, "Y")) {
        progress("PM error=needs-guarded-boot");
        return -1;
    }
    // Match by bound driver (the `identity` attribute needs CONFIG_WATCHDOG_SYSFS,
    // absent on R4); opening a second owner's watchdog fails EBUSY.
    if (glob("/sys/class/watchdog/watchdog*/device/driver", 0, NULL, &nodes)) return -1;
    int count = 0;
    for (size_t i = 0; i < nodes.gl_pathc; ++i) {
        char link[PATH_MAX];
        ssize_t len = readlink(nodes.gl_pathv[i], link, sizeof(link) - 1);
        if (len < 0) continue;
        link[len] = 0;
        const char *drv = strrchr(link, '/');
        if (strcmp(drv ? drv + 1 : link, "qcom_wdt")) continue;
        const char *name = nodes.gl_pathv[i] + strlen("/sys/class/watchdog/");
        int n = snprintf(node, sizeof(node), "/dev/%.*s", (int)strcspn(name, "/"), name);
        if (n < 0 || (size_t)n >= sizeof(node)) { globfree(&nodes); return -1; }
        count++;
    }
    globfree(&nodes);
    if (count != 1) return -1;
    progress("PM watchdog=opening-qcom-driver");
    wd = open(node, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    if (wd < 0) return -1;
    struct watchdog_info info = {0};
    int timeout = seconds, pretimeout = 0;
    if (ioctl(wd, WDIOC_GETSUPPORT, &info) || strcmp((char *)info.identity, "qcom_wdt") ||
        ioctl(wd, WDIOC_SETTIMEOUT, &timeout) || timeout != seconds ||
        ((info.options & WDIOF_PRETIMEOUT) && ioctl(wd, WDIOC_SETPRETIMEOUT, &pretimeout)) ||
        ioctl(wd, WDIOC_KEEPALIVE, 0)) return -1;
    progress("PM watchdog=armed,%ds,no-further-feeding", seconds);
    return 0;
}

static int alarm_arm(int seconds)
{
    char buf[128];
    if (get("/sys/class/rtc/rtc0/name", buf, sizeof(buf)) || !strstr(buf, "pm8xxx")) return -1;
    rtc = open("/dev/rtc0", O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    struct rtc_time rt;
    if (rtc < 0 || ioctl(rtc, RTC_WKALM_RD, &saved_alarm)) return -1;
    if (saved_alarm.enabled) {
        progress("PM error=rtc-already-armed");
        errno = EBUSY;
        return -1;
    }
    if (ioctl(rtc, RTC_RD_TIME, &rt)) return -1;
    struct tm tm = {.tm_sec=rt.tm_sec, .tm_min=rt.tm_min, .tm_hour=rt.tm_hour,
                    .tm_mday=rt.tm_mday, .tm_mon=rt.tm_mon, .tm_year=rt.tm_year};
    time_t when = timegm(&tm) + seconds;
    if (!gmtime_r(&when, &tm)) return -1;
    struct rtc_wkalrm alarm = {.enabled=1};
    alarm.time = (struct rtc_time){.tm_sec=tm.tm_sec, .tm_min=tm.tm_min, .tm_hour=tm.tm_hour,
                                 .tm_mday=tm.tm_mday, .tm_mon=tm.tm_mon, .tm_year=tm.tm_year};
    alarm_owned = true;
    if (ioctl(rtc, RTC_WKALM_SET, &alarm)) return -1;
    struct rtc_wkalrm actual;
    if (ioctl(rtc, RTC_WKALM_RD, &actual) || !actual.enabled ||
        actual.time.tm_sec != alarm.time.tm_sec || actual.time.tm_min != alarm.time.tm_min ||
        actual.time.tm_hour != alarm.time.tm_hour || actual.time.tm_mday != alarm.time.tm_mday ||
        actual.time.tm_mon != alarm.time.tm_mon || actual.time.tm_year != alarm.time.tm_year) return -1;
    progress("PM rtc=armed,rtc-relative-plus%d,readback-matched", seconds);
    return 0;
}

static int finish(void)
{
    // Restore the awake inhibitor before disarming either recovery mechanism.
    if (put("/sys/power/wake_lock", "a9ml_test_awake")) return -1;
    progress("PM cleanup=awake-lock-restored");
    if (pmtest_owned) {
        char selected[256];
        if (put("/sys/power/pm_test", "none") ||
            get("/sys/power/pm_test", selected, sizeof(selected)) || !strstr(selected, "[none]")) return -1;
        pmtest_owned = false;
        progress("PM cleanup=pm-test-none");
    }
    if (alarm_owned && ioctl(rtc, RTC_WKALM_SET, &saved_alarm)) return -1;
    alarm_owned = false;
    progress("PM cleanup=alarm-restored");
    if (wd >= 0) {
        int options = WDIOS_DISABLECARD;
        if (ioctl(wd, WDIOC_SETOPTIONS, &options)) return -1;
        // Magic-close prevents the watchdog core from restarting on close.
        if (write(wd, "V", 1) != 1 || close(wd)) return -1;
        wd = -1;
        progress("PM watchdog=disarmed");
    }
    if (rtc >= 0) { close(rtc); rtc = -1; }
    if (watchdogd_restore()) return -1;
    progress("PM cleanup=awake,alarm-restored,watchdog-disarmed");
    return 0;
}

static bool pmtest_level(const char *level)
{
    return !strcmp(level, "freezer") || !strcmp(level, "devices") || !strcmp(level, "platform");
}

static void wait_token(const char *path, const char *token)
{
    char value[128];
    // Never feed/disarm the WDT while waiting for a host round trip.
    while (get(path, value, sizeof(value)) || strcmp(value, token)) usleep(50000);
}

static int pmtest(const char *level)
{
    char selected[256], delay[32], token[33], chosen[32];
    unsigned char random[16];
    bool cleanup_ack = false;
    int lock = open("/data/local/tmp/a9-pmtest.lock", O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        progress("PM error=pmtest-already-owned-or-lock-failed");
        if (lock >= 0) close(lock);
        return 1;
    }
    // k4 lacks CONFIG_PM_DEBUG. Refuse before watchdogd handoff or WDT arm.
    if (get("/sys/power/pm_test", selected, sizeof(selected)) || !strstr(selected, "[none]") ||
        get("/sys/module/suspend/parameters/pm_test_delay", delay, sizeof(delay)) ||
        strspn(delay, "0123456789") != strlen(delay) || !delay[0] ||
        strtoul(delay, NULL, 10) < 1 || strtoul(delay, NULL, 10) > 5) {
        progress("PM error=needs-pm-debug-idle-test-and-delay1-to5");
        close(lock);
        return 1;
    }
    int reader = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (reader < 0 || getrandom(random, sizeof(random), 0) != sizeof(random) ||
        (unlink(PMTEST_GO) && errno != ENOENT) || (unlink(PMTEST_ACK) && errno != ENOENT)) {
        progress("PM error=pmtest-observer-setup");
        if (reader >= 0) close(reader);
        close(lock);
        return 1;
    }
    for (size_t i = 0; i < sizeof(random); ++i) snprintf(token + 2*i, 3, "%02x", random[i]);
    if (watchdogd_stop()) {
        progress("PM error=pmtest-watchdogd-stop");
        watchdogd_restore();
        close(reader); close(lock);
        return 1;
    }
    if (watchdog_arm(30)) goto leave_armed;
    progress("PM pmtest=ready,level=%s,token=%s,watchdog=armed30", level, token);
    // The host fsyncs its BEGIN record BEFORE granting this permission.
    wait_token(PMTEST_GO, token);
    if (unlink(PMTEST_GO)) goto leave_armed;
    pmtest_owned = true; // A failed/partial selection still requires restore.
    snprintf(chosen, sizeof(chosen), "[%s]", level);
    if (put("/sys/power/pm_test", level) ||
        get("/sys/power/pm_test", selected, sizeof(selected)) || !strstr(selected, chosen) ||
        lseek(reader, 0, SEEK_END) < 0) goto leave_armed;
    progress("PM pmtest=begin,level=%s", level);
    int rc = put("/sys/power/state", "freeze"), saved = errno;
    bool reached = false, complete = false;
    // Confirm the kernel reached its selected test delay, not just an early
    // wake/error that happened to return from the sysfs write. Never dump kmsg.
    for (int i = 0; i < 4096; ++i) {
        char record[8192];
        ssize_t n = read(reader, record, sizeof(record)-1);
        if (!n || (n < 0 && errno == EAGAIN)) { complete = true; break; }
        if (n < 0) break; // EPIPE/lost records is uncertainty, not a pass.
        record[n] = 0;
        if (strstr(record, "suspend debug: Waiting for ")) reached = true;
    }
    close(reader); reader = -1;
    // Restore test selection and inhibitor before accepting any cleanup ACK.
    if (put("/sys/power/wake_lock", "a9ml_test_awake") ||
        put("/sys/power/pm_test", "none") ||
        get("/sys/power/pm_test", selected, sizeof(selected)) || !strstr(selected, "[none]")) goto leave_armed;
    pmtest_owned = false;
    bool passed = !rc && reached && complete;
    progress("PM pmtest=end,level=%s,write_rc=%d,errno=%d,test_delay_seen=%d,log_complete=%d,restored=none",
             level, rc, rc ? saved : 0, reached, complete);
    progress("PM waiting=pmtest-host-ack,token=%s", token);
    // END must be durable on the host before it authorizes WDT disarm.
    wait_token(PMTEST_ACK, token);
    cleanup_ack = true;
    if (unlink(PMTEST_ACK) || finish()) goto leave_armed;
    progress("PM result=pmtest-returned-and-host-acknowledged,level=%s,pass=%d", level, passed);
    close(lock);
    return passed ? 0 : 1;
leave_armed:
    progress("PM error=pmtest-incomplete,errno=%d,cleanup-not-proven", errno);
    // Restore the selector/inhibitor where possible; never disarm without ACK.
    put("/sys/power/wake_lock", "a9ml_test_awake");
    if (pmtest_owned) put("/sys/power/pm_test", "none");
    if (reader >= 0) close(reader);
    // An unexpected close can make watchdog-core ping/restart the deadline.
    // Before ACK, retain the open fd/lock and let the original timer expire.
    if (wd >= 0 && !cleanup_ack) for (;;) pause();
    if (wd >= 0) close(wd); // ACK was received; cleanup is now uncertain
    else watchdogd_restore();
    close(lock);
    return 1;
}

int main(int argc, char **argv)
{
    const char *operation = "observability";
    setvbuf(stdout, NULL, _IONBF, 0);
    bool test = argc == 3 && !strcmp(argv[1], "pmtest");
    if (test && !pmtest_level(argv[2])) {
        fputs("PM error=unsupported-s2idle-pmtest-level,use-freezer-devices-platform-only\n", stderr);
        return 2;
    }
    if (!test && (argc != 2 || (strcmp(argv[1], "rtc") && strcmp(argv[1], "key") &&
        strcmp(argv[1], "rtc-awake") && strcmp(argv[1], "watchdog-awake") &&
        strcmp(argv[1], "watchdog-s2idle")))) {
        fputs("usage: a9ml-suspend rtc|key|rtc-awake|watchdog-awake|watchdog-s2idle|pmtest freezer|devices|platform\n", stderr);
        return 2;
    }
    kmsg = open("/dev/kmsg", O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    if (kmsg < 0) { perror("PM error=kmsg-open"); return 1; }
    progress("PM helper=observable-v1,mode=%s", argv[1]);
    char buf[128];
    // The previous run lost its RAM /data log. Refuse another blind attempt.
    if (get("/sys/module/pstore/parameters/backend", buf, sizeof(buf)) || strcmp(buf, "ramoops") ||
        get("/sys/module/printk/parameters/console_suspend", buf, sizeof(buf)) || strcmp(buf, "N")) {
        progress("PM error=needs-ramoops-and-no-console-suspend");
        return 1;
    }
    progress("PM logging=kmsg,ramoops-console-active");
    // Existing beacon is RAM/PMIC reset-mode state, never write modem stores.
    if (get("/sys/module/qcom_pon/parameters/beacon", buf, sizeof(buf)) || strcmp(buf, "2") ||
        put("/sys/power/wake_lock", "a9ml_test_awake")) {
        progress("PM error=beacon-or-awake-lock");
        return 1;
    }
    if (test) return pmtest(argv[2]);
    if (unlink(ACK) && errno != ENOENT) {
        progress("PM error=old-ack-remove,errno=%d", errno);
        return 1;
    }
    operation = "watchdogd-stop";
    if (watchdogd_stop()) goto fail;
    operation = "watchdog-arm";
    bool suspended_watchdog_test = !strcmp(argv[1], "watchdog-s2idle");
    if (watchdog_arm(suspended_watchdog_test ? 15 : 30)) goto fail;
    bool watchdog_test = !strncmp(argv[1], "watchdog-", 9);
    operation = "rtc-arm";
    // During WDT qualification, retain an RTC backup (+20) after the intended
    // WDT bite (+15). A reset after RTC wake does not qualify asleep counting.
    if (strcmp(argv[1], "watchdog-awake") && alarm_arm(suspended_watchdog_test ? 20 : 10)) goto fail;
    if (!strcmp(argv[1], "watchdog-awake")) {
        progress("PM waiting=hardware-reset-to-bootloader-within30s");
        for (;;) pause();
    }
    if (!strcmp(argv[1], "rtc-awake")) {
        operation = "rtc-event-awake";
        struct pollfd p = {.fd=rtc, .events=POLLIN};
        unsigned long event;
        if (poll(&p, 1, 15000) != 1 || read(rtc, &event, sizeof(event)) != sizeof(event) ||
            !(event & RTC_AF)) goto fail;
        progress("PM rtc_event=alarm");
        return finish() ? 1 : 0;
    }
    // Leave a9ml_test_awake held: direct freeze is deliberate and Android
    // autosuspend must not race this one-shot test or immediately re-suspend.
    double bt = now(CLOCK_BOOTTIME), mt = now(CLOCK_MONOTONIC);
    progress("PM entering=s2idle");
    operation = "s2idle-write";
    if (put("/sys/power/state", "freeze")) goto fail;
    double suspended = (now(CLOCK_BOOTTIME) - bt) - (now(CLOCK_MONOTONIC) - mt);
    progress("PM returned=s2idle,suspended_seconds=%.3f", suspended);
    if (watchdog_test) {
        progress("PM waiting=hardware-reset-after-s2idle,no-disarm");
        for (;;) pause();
    }
    struct pollfd p = {.fd=rtc, .events=POLLIN};
    unsigned long event = 0;
    bool rtc_event = poll(&p, 1, 0) == 1 &&
                     read(rtc, &event, sizeof(event)) == sizeof(event) && (event & RTC_AF);
    progress("PM rtc_event=%s", rtc_event ? "alarm" : "none");
    progress("PM waiting=host-adb-ack,watchdog-still-armed");
    // A device-side return or UDC state cannot prove host adb recovered.
    while (access(ACK, F_OK)) usleep(100000);
    operation = "host-ack-cleanup";
    progress("PM host_ack=received");
    if (unlink(ACK) || finish()) goto fail;
    // A key wake is allowed to precede the backup RTC alarm. An immediate
    // wake or absent alarm is diagnostic evidence, never an RTC sleep pass.
    bool passed = suspended >= 1.0 && (!strcmp(argv[1], "key") ? !rtc_event : rtc_event);
    progress(passed ? "PM result=returned-and-host-acknowledged" : "PM result=early-or-non-rtc-wake");
    return passed ? 0 : 1;
fail:
    progress("PM error=%s,errno=%d", operation, errno);
    // Unexpected death/failed cleanup leaves the hardware timeout in force.
    // On an ordinary pre-suspend failure, clean up and return the exact error.
    if (finish()) progress("PM cleanup=failed,watchdog-may-remain-armed-or-service-not-restored");
    return 1;
}
