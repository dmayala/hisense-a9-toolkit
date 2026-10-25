// SPDX-License-Identifier: GPL-2.0-only
/* SNS client protocol steps (moved out of s2-sns-probe.c unchanged in wire behaviour; the
 * byte-exact probe tests pin that). Layouts: s2-sns-layouts.md. */
#include "s2-client.h"
#include <stdlib.h>
#include <string.h>

static const struct s2_suid suid_lookup = { 0xababababababababull, 0xababababababababull };

struct s2_conn *s2_conn_open(const struct s2_link *L)
{
    struct s2_conn *c = calloc(1, sizeof(*c));
    if (!c) return NULL;
    c->h = L->open(L->ctx);
    if (c->h < 0) { free(c); return NULL; }
    return c;
}
void s2_conn_close(const struct s2_link *L, struct s2_conn *c)
{
    if (!c) return;
    L->close(L->ctx, c->h);
    free(c);
}
int s2_request(const struct s2_link *L, struct s2_conn *c, const uint8_t *pb, size_t pbn, FILE *log, const char *what)
{
    uint8_t frame[S2_PAYLOAD_MAX + 32];
    struct s2_qmi_resp r;
    uint16_t txn = ++c->txn;
    size_t n = s2_qmi_request(frame, sizeof(frame), txn, pb, pbn);
    if (!n || L->send(L->ctx, c->h, frame, n)) { fprintf(log, "S2-PROBE-FAIL send %s\n", what); return -1; }
    int64_t end = L->now_ms(L->ctx) + S2_RESP_MS;
    for (;;) {
        int64_t left = end - L->now_ms(L->ctx);
        if (left <= 0) { fprintf(log, "S2-PROBE-FAIL response-timeout %s\n", what); return -1; }
        int m = L->recv(L->ctx, c->h, c->rx, sizeof(c->rx), (int)left);
        if (m < 0) { fprintf(log, "S2-PROBE-FAIL transport %s\n", what); return -1; }
        if (!m) continue;
        if (c->rx[0] == 4) {   /* an indication before the response: keep the first, refuse a second */
            if (c->early_len || (size_t)m > sizeof(c->early)) { fprintf(log, "S2-PROBE-FAIL early-indications %s\n", what); return -1; }
            memcpy(c->early, c->rx, (size_t)m); c->early_len = (size_t)m; continue;
        }
        if (s2_qmi_decode_response(c->rx, (size_t)m, txn, &r)) { fprintf(log, "S2-PROBE-FAIL bad-response %s\n", what); return -1; }
        if (r.result || r.error || (r.has_result && r.sns_result)) {
            fprintf(log, "S2-PROBE-FAIL qmi-error %s result=%u error=%u sns=%u\n", what, r.result, r.error, r.sns_result);
            return -1;
        }
        if (r.has_client_id) { if (c->has_client && c->client_id != r.client_id) return -1; c->client_id = r.client_id; c->has_client = 1; }
        return 0;
    }
}
int s2_next_events(const struct s2_link *L, struct s2_conn *c, int64_t end, struct s2_event_msg **out)
{
    for (;;) {
        int n;
        if (c->early_len) { memcpy(c->rx, c->early, c->early_len); n = (int)c->early_len; c->early_len = 0; goto have; }
        int64_t left = end - L->now_ms(L->ctx);
        if (left <= 0) return 1;
        n = L->recv(L->ctx, c->h, c->rx, sizeof(c->rx), (int)left);
        if (n < 0) return -1;
        if (!n) continue;
have:
        if (c->rx[0] == 2) {
            if (n < 7) return -1;
            struct s2_qmi_resp response;
            uint16_t txn = (uint16_t)(c->rx[1] | c->rx[2] << 8);
            if (s2_qmi_decode_response(c->rx, (size_t)n, txn, &response) || response.result || response.error ||
                (response.has_result && response.sns_result)) return -1;
            continue;
        }
        if (c->rx[0] != 4) continue;
        uint64_t cid; const uint8_t *pl; size_t pll;
        if (s2_qmi_decode_indication(c->rx, (size_t)n, &cid, &pl, &pll)) return -1;
        if (c->has_client && cid != c->client_id) return -1;
        if (s2_decode_event_msg(pl, pll, &c->m)) return -1;
        *out = &c->m;
        return 0;
    }
}
int s2_lookup(const struct s2_link *L, const char *dt, struct s2_suid *out, int timeout_ms, FILE *log)
{
    uint8_t q[96], pb[256];
    struct s2_conn *c = s2_conn_open(L);
    int found = -1, empty = 0;
    if (!c) { fprintf(log, "S2-PROBE-FAIL open suid %s\n", dt); return -1; }
    size_t qn = s2_suid_req(q, sizeof(q), dt, 1);
    size_t n = qn ? s2_client_request(pb, sizeof(pb), suid_lookup, S2_MSGID_SUID_REQ, S2_DELIVERY_WAKEUP, q, qn, 0, 0) : 0;
    if (!n || s2_request(L, c, pb, n, log, "suid-req")) goto out;
    int64_t end = L->now_ms(L->ctx) + timeout_ms;
    for (;;) {
        struct s2_event_msg *m;
        int rc = s2_next_events(L, c, end, &m);
        if (rc) { if (rc > 0) { fprintf(log, "S2-SUID dt=%s count=0 empty_event=%d\n", dt, empty); found = 0; } else fprintf(log, "S2-PROBE-FAIL suid-event %s\n", dt); goto out; }
        for (size_t i = 0; i < m->n; i++) {
            struct s2_suid_event e;
            if (m->ev[i].msg_id != S2_MSGID_SUID_EVENT) continue;
            if (s2_decode_suid_event(m->ev[i].payload, m->ev[i].payload_len, &e)) { fprintf(log, "S2-PROBE-FAIL suid-decode %s\n", dt); goto out; }
            if (strcmp(e.data_type, dt)) continue;
            if (!e.n) { empty = 1; continue; }   /* register_updates: a later event may name the sensor */
            fprintf(log, "S2-SUID dt=%s count=%zu", dt, e.n);
            for (size_t k = 0; k < e.n; k++) fprintf(log, " suid=%016llx:%016llx", (unsigned long long)e.suid[k].low, (unsigned long long)e.suid[k].high);
            fputc('\n', log);
            *out = e.suid[0];
            found = 1;
            goto out;
        }
    }
out:
    s2_conn_close(L, c);
    return found;
}
int s2_attributes(const struct s2_link *L, const char *dt, struct s2_suid s, struct s2_attrs *a, int timeout_ms, FILE *log)
{
    uint8_t pb[256], empty = 0;
    struct s2_conn *c = s2_conn_open(L);
    int rc = -1;
    if (!c) return -1;
    size_t n = s2_client_request(pb, sizeof(pb), s, S2_MSGID_ATTR_REQ, S2_DELIVERY_NO_WAKEUP, &empty, 0, 0, 0);
    if (!n || s2_request(L, c, pb, n, log, "attr-req")) goto out;
    int64_t end = L->now_ms(L->ctx) + timeout_ms;
    for (;;) {
        struct s2_event_msg *m;
        int e = s2_next_events(L, c, end, &m);
        if (e) { fprintf(log, "S2-PROBE-FAIL attr-%s %s\n", e > 0 ? "timeout" : "event", dt); goto out; }
        for (size_t i = 0; i < m->n; i++) {
            if (m->ev[i].msg_id != S2_MSGID_ATTR_EVENT) continue;
            if (s2_decode_attr_event(m->ev[i].payload, m->ev[i].payload_len, a)) { fprintf(log, "S2-PROBE-FAIL attr-decode %s\n", dt); goto out; }
            fprintf(log, "S2-ATTR dt=%s name=\"%s\" vendor=\"%s\" type=\"%s\" stream_type=%lld available=%d rates=%zu\n",
                    dt, a->name, a->vendor, a->type, a->has_stream_type ? (long long)a->stream_type : -1LL,
                    a->has_available ? a->available : -1, a->n_rates);
            rc = 0; goto out;
        }
    }
out:
    s2_conn_close(L, c);
    return rc;
}
struct s2_conn *s2_stream_start(const struct s2_link *L, const char *dt, struct s2_suid s, int on_change, float hz, FILE *log)
{
    uint8_t cfg[16], pb[256], empty = 0;
    struct s2_conn *c = s2_conn_open(L);
    if (!c) return NULL;
    size_t cn = on_change ? 0 : s2_sensor_config(cfg, sizeof(cfg), hz);
    size_t n = s2_client_request(pb, sizeof(pb), s, on_change ? S2_MSGID_ON_CHANGE_CONFIG : S2_MSGID_SENSOR_CONFIG,
                                 S2_DELIVERY_NO_WAKEUP, on_change ? &empty : cfg, cn, 1, 0);
    if ((!on_change && !cn) || !n || s2_request(L, c, pb, n, log, "config")) { s2_conn_close(L, c); return NULL; }
    fprintf(log, "S2-STREAM-START dt=%s mode=%s rate=%.1f\n", dt, on_change ? "on-change" : "streaming", (double)hz);
    return c;
}
int s2_stream_stop(const struct s2_link *L, struct s2_conn *c, struct s2_suid s, FILE *log)
{
    uint8_t pb[256];
    int rc = -1;
    if (!c) return -1;
    size_t n = s2_client_request(pb, sizeof(pb), s, S2_MSGID_DISABLE_REQ, S2_DELIVERY_NO_WAKEUP, NULL, 0, 0, 0);
    if (n && !s2_request(L, c, pb, n, log, "disable")) rc = 0;
    s2_conn_close(L, c);
    return rc;
}
