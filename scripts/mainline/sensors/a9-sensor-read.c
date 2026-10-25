/* a9-sensor-read.c -- S2c test client: list the framework's sensors, then read the default
 * light sensor through the public NDK (ASensorManager -> sensorservice -> our AIDL HAL) for
 * a fixed time and print what arrived. Runs as the shell user (light needs no permission).
 *
 *   a9-sensor-read <seconds> [period_us] [light|accel|mag|proximity]      (default period 200000 = 5 Hz request)
 *
 * Output (one line each, parsed by the S2c run script):
 *   S2C-SENSOR handle=0x.. type=.. name="..." vendor="..." version=.. range=.. resolution=..
 *              power=.. minDelay=.. fifoMax=.. fifoReserved=.. mode=..
 *   S2C-LIGHT-DEFAULT handle=0x.. name="..."
 *   S2C-READ events=N seconds=S rate_hz=R lux_min=.. lux_max=.. lux_last=.. (historical field names; selected sensor scalar/X) nonmonotonic=K
 *            future=F max_age_ms=A first_after_ms=T
 *   S2C-READ-RESULT PASS|FAIL <why>
 * PASS = at least one event, timestamps strictly increasing, none in the future of
 * CLOCK_BOOTTIME (+5 ms slack), no NaN/negative lux. Rate and lux are reported, not judged
 * here (the run script judges the rate against the S2a ~8 Hz).
 * The selected default sensor is enabled alone; default remains light.
 * B2-SAMPLE prints selected XYZ/scalar and units once per second. */
#include <android/looper.h>
#include <android/sensor.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int64_t boottime_ns(void)
{
    struct timespec t;
    clock_gettime(CLOCK_BOOTTIME, &t);
    return (int64_t)t.tv_sec * 1000000000LL + t.tv_nsec;
}

static void clean(char *dst, size_t cap, const char *s)
{
    size_t o = 0;
    for (; s && *s && o + 1 < cap; s++) dst[o++] = (*s < 0x20 || *s > 0x7e || *s == '"') ? '?' : *s;
    dst[o] = 0;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc < 2 || argc > 4) { fprintf(stderr, "usage: %s <seconds> [period_us] [light|accel|mag|proximity]\n", argv[0]); return 2; }
    int secs = atoi(argv[1]);
    int period = argc >= 3 ? atoi(argv[2]) : 200000;
    int type = ASENSOR_TYPE_LIGHT;
    if (argc == 4) {
        if (!strcmp(argv[3], "accel")) type = ASENSOR_TYPE_ACCELEROMETER;
        else if (!strcmp(argv[3], "mag")) type = ASENSOR_TYPE_MAGNETIC_FIELD;
        else if (!strcmp(argv[3], "proximity")) type = ASENSOR_TYPE_PROXIMITY;
        else if (strcmp(argv[3], "light")) return 2;
    }
    int vector = type == ASENSOR_TYPE_ACCELEROMETER || type == ASENSOR_TYPE_MAGNETIC_FIELD;
    if (secs < 1 || secs > 600 || period < 0) return 2;
    ASensorManager *sm = ASensorManager_getInstanceForPackage("com.android.shell");
    if (!sm) { puts("S2C-READ-RESULT FAIL no-sensor-manager"); return 1; }
    ASensorList list;
    int n = ASensorManager_getSensorList(sm, &list);
    for (int i = 0; i < n; i++) {
        const ASensor *s = list[i];
        char name[128], vendor[64];
        clean(name, sizeof name, ASensor_getName(s));
        clean(vendor, sizeof vendor, ASensor_getVendor(s));
        printf("S2C-SENSOR handle=0x%x type=%d name=\"%s\" vendor=\"%s\" version=- range=- resolution=%g power=- "
               "minDelay=%d fifoMax=%d fifoReserved=%d mode=%d wakeup=%d\n",
               ASensor_getHandle(s), ASensor_getType(s), name, vendor, ASensor_getResolution(s),
               ASensor_getMinDelay(s), ASensor_getFifoMaxEventCount(s), ASensor_getFifoReservedEventCount(s),
               ASensor_getReportingMode(s), ASensor_isWakeUpSensor(s));
    }
    printf("S2C-SENSORS count=%d\n", n);
    const ASensor *light = ASensorManager_getDefaultSensor(sm, type);
    if (!light) { puts("S2C-READ-RESULT FAIL no-default-sensor"); return 1; }
    char lname[128];
    clean(lname, sizeof lname, ASensor_getName(light));
    printf("S2C-%s-DEFAULT handle=0x%x name=\"%s\"\n", type == ASENSOR_TYPE_LIGHT ? "LIGHT" : "SENSOR", ASensor_getHandle(light), lname);
    ALooper *lp = ALooper_prepare(ALOOPER_PREPARE_ALLOW_NON_CALLBACKS);
    ASensorEventQueue *q = ASensorManager_createEventQueue(sm, lp, 1, NULL, NULL);
    if (!q) { puts("S2C-READ-RESULT FAIL no-event-queue"); return 1; }
    int64_t t0 = boottime_ns();
    if (ASensorEventQueue_registerSensor(q, light, period, 0) < 0) {
        puts("S2C-READ-RESULT FAIL register");
        ASensorManager_destroyEventQueue(sm, q);
        return 1;
    }
    long events = 0, nonmono = 0, future = 0, bad = 0;
    int64_t next_print = 0;
    int64_t last_ts = 0, first_after = -1, max_age = 0;
    float mn = INFINITY, mx = -INFINITY, lastv = NAN;
    int64_t end = t0 + (int64_t)secs * 1000000000LL;
    while (boottime_ns() < end) {
        int64_t left_ms = (end - boottime_ns()) / 1000000;
        ALooper_pollOnce(left_ms > 500 ? 500 : (int)(left_ms > 0 ? left_ms : 1), NULL, NULL, NULL);
        ASensorEvent ev[16];
        ssize_t k;
        while ((k = ASensorEventQueue_getEvents(q, ev, 16)) > 0) {
            int64_t now = boottime_ns();
            for (ssize_t i = 0; i < k; i++) {
                if (ev[i].type != type) continue;
                float v = ev[i].data[0];
                if (!isfinite(v) || (!vector && v < 0)) bad++;
                if (vector && (!isfinite(ev[i].data[1]) || !isfinite(ev[i].data[2]))) bad++;
                if (now >= next_print || type == ASENSOR_TYPE_PROXIMITY) {
                    if (vector) printf("B2-SAMPLE type=%d xyz=(%.4f,%.4f,%.4f) norm=%.4f units=%s timestamp_ns=%lld\n",
                        type, v, ev[i].data[1], ev[i].data[2],
                        sqrtf(v*v + ev[i].data[1]*ev[i].data[1] + ev[i].data[2]*ev[i].data[2]),
                        type == ASENSOR_TYPE_ACCELEROMETER ? "m/s2" : "uT", (long long)ev[i].timestamp);
                    else printf("B2-SAMPLE type=%d scalar=%.4f units=%s timestamp_ns=%lld\n", type, v,
                        type == ASENSOR_TYPE_PROXIMITY ? "cm" : "lux", (long long)ev[i].timestamp);
                    next_print = now + 1000000000LL;
                }
                if (events && ev[i].timestamp <= last_ts) nonmono++;
                if (ev[i].timestamp > now + 5000000) future++;
                if (now - ev[i].timestamp > max_age) max_age = now - ev[i].timestamp;
                if (first_after < 0) first_after = now - t0;
                last_ts = ev[i].timestamp;
                if (v < mn) mn = v;
                if (v > mx) mx = v;
                lastv = v;
                events++;
            }
        }
    }
    ASensorEventQueue_disableSensor(q, light);
    ASensorManager_destroyEventQueue(sm, q);
    double dur = (boottime_ns() - t0) / 1e9;
    printf("S2C-READ events=%ld seconds=%.1f rate_hz=%.2f lux_min=%.2f lux_max=%.2f lux_last=%.2f nonmonotonic=%ld "
           "future=%ld bad=%ld max_age_ms=%lld first_after_ms=%lld\n",
           events, dur, events / dur, events ? mn : 0, events ? mx : 0, events ? lastv : 0, nonmono, future, bad,
           (long long)(max_age / 1000000), (long long)(first_after < 0 ? -1 : first_after / 1000000));
    const char *why = !events ? "no-events" : nonmono ? "non-monotonic" : future ? "future-timestamps" : bad ? "bad-values" : NULL;
    if (why) { printf("S2C-READ-RESULT FAIL %s\n", why); return 1; }
    puts("S2C-READ-RESULT PASS");
    return 0;
}
