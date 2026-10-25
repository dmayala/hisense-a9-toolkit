// SPDX-License-Identifier: GPL-2.0-only
/* S2a probe: first real sensor samples from the A9 sensors PD over QMI SNS (400).
 *
 * Bounded, one shot, QRTR datagrams only (no FastRPC, files, persist, diag,
 * calibration or registry writes from this side). Sequence, each on a fresh
 * QRTR socket (stock libssc uses one connection per stream):
 *   1. SUID lookup (msg 512, default_only) for ambient_light, accel, proximity
 *   2. attribute request (msg 1) for each SUID found
 *   3. ambient_light: on-change config (514) if STREAM_TYPE==1, else 5 Hz (513); 10 s
 *   4. accel: 50 Hz (513); 5 s
 *   5. disable (msg 10) and close after each stream
 * PASS needs >=1 finite ALS sample >= 0 and >=10 accel samples with mean |a|
 * within 9.8 +/- 1.5 m/s^2 (phone lying still), no SNS error event, no QMI error.
 * Proximity is lookup + attributes only (never enabled in S2a).
 */
#define _POSIX_C_SOURCE 200809L
#include "s2-client.h"
#include "s2-qrtr.h"
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <stdlib.h>
#include <unistd.h>

#define S2_SUID_MS 5000
#define S2_ATTR_MS 3000
#define S2_ALS_MS 10000
#define S2_ACCEL_MS 5000
#define S2_ACCEL_HZ 50.0f
#define S2_ALS_HZ 5.0f
#define S2_TOTAL_MS 45000

struct stream_result { unsigned events, configs, samples; double first[3], sum_norm, min0, max0; int bad; };

static int stream(const struct s2_link *L, const char *dt, struct s2_suid s, int on_change, float hz,
                  int ms, struct stream_result *res, FILE *log)
{
    int rc = -1;
    memset(res, 0, sizeof(*res));
    struct s2_conn *c = s2_stream_start(L, dt, s, on_change, hz, log);
    if (!c) return -1;
    int64_t end = L->now_ms(L->ctx) + ms;
    for (;;) {
        struct s2_event_msg *m;
        int e = s2_next_events(L, c, end, &m);
        if (e > 0) break;
        if (e < 0) { fprintf(log, "S2-PROBE-FAIL stream-event %s\n", dt); goto disable; }
        for (size_t i = 0; i < m->n; i++) {
            struct s2_event *ev = &m->ev[i];
            if (ev->msg_id == S2_MSGID_ERROR_EVENT) {
                uint32_t err = 0; s2_decode_error_event(ev->payload, ev->payload_len, &err);
                fprintf(log, "S2-PROBE-FAIL sns-error dt=%s error=%u\n", dt, err); goto disable;
            }
            if (ev->msg_id == S2_MSGID_PHYSICAL_CONFIG_EVENT) { res->configs++; continue; }
            if (ev->msg_id != S2_MSGID_SENSOR_EVENT) continue;
            struct s2_sensor_event se;
            if (s2_decode_sensor_event(ev->payload, ev->payload_len, &se) || !se.n) { res->bad++; continue; }
            int finite = 1; double norm = 0;
            for (size_t k = 0; k < se.n; k++) { if (!isfinite(se.data[k])) finite = 0; if (k < 3) norm += (double)se.data[k] * se.data[k]; }
            if (!finite) { res->bad++; continue; }
            if (!res->samples) { for (size_t k = 0; k < 3 && k < se.n; k++) res->first[k] = se.data[k]; res->min0 = res->max0 = se.data[0]; }
            if (se.data[0] < res->min0) res->min0 = se.data[0];
            if (se.data[0] > res->max0) res->max0 = se.data[0];
            res->sum_norm += sqrt(norm);
            res->samples++;
            if (res->samples <= 3 || (res->samples % 50) == 0)
                fprintf(log, "S2-SAMPLE dt=%s n=%u ts=%llu v0=%.3f v1=%.3f v2=%.3f count=%zu status=%u\n", dt, res->samples,
                        (unsigned long long)ev->timestamp, (double)se.data[0], se.n > 1 ? (double)se.data[1] : 0.0,
                        se.n > 2 ? (double)se.data[2] : 0.0, se.n, se.has_status ? se.status : 0u);
        }
        res->events += (unsigned)m->n;
    }
    rc = 0;
disable:
    if (s2_stream_stop(L, c, s, log)) rc = -1;
    fprintf(log, "S2-STREAM-END dt=%s samples=%u configs=%u bad=%u min_v0=%.3f max_v0=%.3f mean_norm=%.3f rc=%d\n", dt,
            res->samples, res->configs, res->bad, res->min0, res->max0, res->samples ? res->sum_norm / res->samples : 0.0, rc);
    return rc;
}
int s2_probe_run(const struct s2_link *L, FILE *log)
{
    struct s2_suid als = { 0, 0 }, acc = { 0, 0 }, prox = { 0, 0 };
    struct s2_attrs a_als, a_acc, a_prox;
    struct stream_result r_als, r_acc;
    int64_t start = L->now_ms(L->ctx);
    int f_als = s2_lookup(L, "ambient_light", &als, S2_SUID_MS, log);
    int f_acc = f_als < 0 ? -1 : s2_lookup(L, "accel", &acc, S2_SUID_MS, log);
    int f_prox = f_acc < 0 ? -1 : s2_lookup(L, "proximity", &prox, S2_SUID_MS, log);
    if (f_als < 0 || f_acc < 0 || f_prox < 0) goto fail;
    if (f_als != 1 || f_acc != 1) { fprintf(log, "S2-PROBE-FAIL missing-sensor als=%d accel=%d\n", f_als, f_acc); goto fail; }
    if (s2_attributes(L, "ambient_light", als, &a_als, S2_ATTR_MS, log) || s2_attributes(L, "accel", acc, &a_acc, S2_ATTR_MS, log)) goto fail;
    if (f_prox == 1 && s2_attributes(L, "proximity", prox, &a_prox, S2_ATTR_MS, log)) goto fail;
    /* Review 79j N1: only the expected parts are enabled. */
    {
        const struct { const char *dt; const struct s2_attrs *a; int present; } chk[] = {
            { "ambient_light", &a_als, 1 }, { "accel", &a_acc, 1 }, { "proximity", &a_prox, f_prox == 1 } };
        for (size_t i = 0; i < 3; i++) {
            if (!chk[i].present) continue;
            int ok = s2_part_expected(chk[i].dt, chk[i].a->name) == 1;
            fprintf(log, "S2-PART dt=%s name=\"%s\" expected=%s match=%d\n", chk[i].dt, chk[i].a->name, s2_part_tokens(chk[i].dt), ok);
            if (!ok) { fprintf(log, "S2-PROBE-FAIL unexpected-part dt=%s\n", chk[i].dt); goto fail; }
        }
    }
    int als_on_change = a_als.has_stream_type && a_als.stream_type == 1;
    if (stream(L, "ambient_light", als, als_on_change, S2_ALS_HZ, S2_ALS_MS, &r_als, log)) goto fail;
    if (stream(L, "accel", acc, 0, S2_ACCEL_HZ, S2_ACCEL_MS, &r_acc, log)) goto fail;
    if (L->now_ms(L->ctx) - start > S2_TOTAL_MS) { fprintf(log, "S2-PROBE-FAIL overall-time\n"); goto fail; }
    double mean = r_acc.samples ? r_acc.sum_norm / r_acc.samples : 0;
    int als_ok = r_als.samples >= 1 && r_als.min0 >= 0.0;
    int acc_ok = r_acc.samples >= 10 && mean >= 8.3 && mean <= 11.3;
    fprintf(log, "S2-PROBE-RESULT als_samples=%u als_min=%.3f als_max=%.3f accel_samples=%u accel_mean_norm=%.3f proximity_found=%d\n",
            r_als.samples, r_als.min0, r_als.max0, r_acc.samples, mean, f_prox);
    if (!als_ok || !acc_ok) { fprintf(log, "S2-PROBE-FAIL criteria als=%d accel=%d\n", als_ok, acc_ok); goto fail; }
    fprintf(log, "S2-PROBE-PASS\n");
    return 0;
fail:
    fprintf(log, "S2-PROBE-DONE rc=1\n");
    return 1;
}

#ifndef S2_PROBE_NO_MAIN
/* s2-sns-probe 400            the S2a probe
 * s2-sns-probe --wait 400 MS  readiness only: exit 0 once exactly one SNS 400 v1 is advertised within MS */
int main(int argc, char **argv)
{
    struct s2_qrtr Q; struct s2_link L;
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc == 4 && !strcmp(argv[1], "--wait") && !strcmp(argv[2], "400")) {
        char *e; long ms = strtol(argv[3], &e, 10);
        if (*e || ms < 0 || ms > 600000) return 2;
        if (s2_qrtr_init(&Q, &L, (int)ms)) { puts("S2-WAIT-FAIL no-unique-sns-400"); return 1; }
        printf("S2-SNS node=%u port=%u\n", Q.node, Q.port);
        s2_qrtr_destroy(&Q);
        return 0;
    }
    if (argc != 2 || strcmp(argv[1], "400")) return 2;
    if (s2_qrtr_init(&Q, &L, 0)) { puts("S2-PROBE-FAIL no-unique-sns-400"); return 1; }
    printf("S2-SNS node=%u port=%u\n", Q.node, Q.port);
    int rc = s2_probe_run(&L, stdout);
    s2_qrtr_destroy(&Q);
    return rc;
}
#endif
