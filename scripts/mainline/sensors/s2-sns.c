// SPDX-License-Identifier: GPL-2.0-only
/* Minimal SNS client encoders/decoders. Layout sources (golden stock vendor,
 * Ghidra addresses = file VA + 0x100000), details in s2-sns-layouts.md:
 *   libsnsapi sns_client_request_msg::SerializeWithCachedSizes 0x2249b0
 *     1 suid (msg), 2 msg_id (fixed32), 3 susp_config (msg), 4 request (msg)
 *   suspend_config 0x222ce8: 1 client_proc_type (enum, member +0x2c),
 *     2 delivery_type (enum, member +0x28), 3 nowakeup_msg_ids (fixed32)
 *   sns_std_suid 0x207ad8: 1 suid_low, 2 suid_high (fixed64)
 *   sns_std_request 0x20ec3c: 1 batching (msg), 2 payload (bytes), 3 is_passive (bool)
 *   sns_std_request_batch_spec 0x20daf0: 1 batch_period, 2 flush_period (uint32), 3, 4 (bool)
 *   sns_suid_req 0x21cfd0: 1 data_type (string), 2 register_updates, 3 default_only (bool)
 *   sns_std_sensor_config 0x1d2e80: 1 sample_rate (float)
 *   sns_client_event_msg 0x227894: 1 suid, 2 events (repeated msg)
 *   ..._sns_client_event 0x225f2c: 1 msg_id (fixed32), 2 timestamp (fixed64), 3 payload (bytes)
 *   sns_suid_event 0x21e708: 1 data_type (string), 2 suid (repeated msg)
 *   sns_std_sensor_event 0x1d3e04: 1 data (repeated float), 2 status (enum)
 *   sns_std_attr_event 0x210ec0: 1 attributes; sns_std_attr 0x20c8b0: 1 attr_id (int32), 2 value;
 *   sns_std_attr_value 0x209080: 1 values; _data 0x20ad5c: 1 subtype, 2 str, 3 flt, 4 sint (sfixed64), 5 boolean
 *   sns_std_error_event 0x212084: 1 error (enum)
 *   QMI: SNS_CLIENT_SVC_qmi_idl_service_object_v01 (Ghidra 0x264570): service 400,
 *     req 0x20 (TLV 0x01 u16-len array <=1000, TLV 0x10 u8), resp 0x20 (0x02 result,
 *     0x10 u64 client_id, 0x11 u32 result), ind 0x21 (0x01 u64, 0x02 array <=1000),
 *     ind 0x22 (0x01 u64, 0x02 array <=62000). libssc send_request (0x114248)
 *     sets use_jumbo_report_valid=1, use_jumbo_report=1 (strh 0x101 @ +1004).
 */
#include "s2-sns.h"
#include <string.h>

static void put(struct s2_buf *b, const void *d, size_t n)
{
    if (b->err || n > b->cap - b->n) { b->err = 1; return; }
    memcpy(b->p + b->n, d, n); b->n += n;
}
void s2_pb_varint(struct s2_buf *b, uint64_t v)
{
    uint8_t t[10]; size_t n = 0;
    do { uint8_t c = v & 0x7f; v >>= 7; t[n++] = c | (v ? 0x80 : 0); } while (v);
    put(b, t, n);
}
void s2_pb_tag(struct s2_buf *b, unsigned field, unsigned wire) { s2_pb_varint(b, ((uint64_t)field << 3) | wire); }
static void le(struct s2_buf *b, uint64_t v, unsigned n)
{
    uint8_t t[8]; for (unsigned i = 0; i < n; i++) t[i] = (uint8_t)(v >> (8 * i)); put(b, t, n);
}
void s2_pb_fixed32(struct s2_buf *b, unsigned f, uint32_t v) { s2_pb_tag(b, f, 5); le(b, v, 4); }
void s2_pb_fixed64(struct s2_buf *b, unsigned f, uint64_t v) { s2_pb_tag(b, f, 1); le(b, v, 8); }
void s2_pb_bool(struct s2_buf *b, unsigned f, int v) { s2_pb_tag(b, f, 0); s2_pb_varint(b, v ? 1 : 0); }
void s2_pb_enum(struct s2_buf *b, unsigned f, uint32_t v) { s2_pb_tag(b, f, 0); s2_pb_varint(b, v); }
void s2_pb_float(struct s2_buf *b, unsigned f, float v)
{
    uint32_t u; memcpy(&u, &v, 4); s2_pb_tag(b, f, 5); le(b, u, 4);
}
void s2_pb_bytes(struct s2_buf *b, unsigned f, const void *d, size_t len)
{
    s2_pb_tag(b, f, 2); s2_pb_varint(b, len); put(b, d, len);
}
static size_t done(struct s2_buf *b) { return b->err ? 0 : b->n; }

size_t s2_suid_req(uint8_t *out, size_t cap, const char *data_type, int default_only)
{
    struct s2_buf b = { out, 0, cap, 0 };
    size_t n = data_type ? strlen(data_type) : 0;
    if (!n || n > 63) return 0;
    s2_pb_bytes(&b, 1, data_type, n);
    s2_pb_bool(&b, 2, 1);            /* register_updates = true (request_suid 0x116184) */
    s2_pb_bool(&b, 3, default_only); /* default_only = caller (request_suid param) */
    return done(&b);
}
size_t s2_sensor_config(uint8_t *out, size_t cap, float rate)
{
    struct s2_buf b = { out, 0, cap, 0 };
    if (!(rate > 0.0f) || rate > 1000.0f) return 0;
    s2_pb_float(&b, 1, rate);
    return done(&b);
}
size_t s2_client_request(uint8_t *out, size_t cap, struct s2_suid suid, uint32_t msg_id,
                         uint32_t delivery, const uint8_t *payload, size_t plen,
                         int with_batching, uint32_t batch_period_us)
{
    struct s2_buf b = { out, 0, cap, 0 };
    uint8_t s[32], c[16], r[S2_PAYLOAD_MAX], bs[16];
    struct s2_buf sb = { s, 0, sizeof(s), 0 }, cb = { c, 0, sizeof(c), 0 },
                  rb = { r, 0, sizeof(r), 0 }, bb = { bs, 0, sizeof(bs), 0 };
    if (delivery > 1 || plen > S2_PAYLOAD_MAX || (plen && !payload)) return 0;
    s2_pb_fixed64(&sb, 1, suid.low); s2_pb_fixed64(&sb, 2, suid.high);
    s2_pb_enum(&cb, 1, S2_PROC_APSS); s2_pb_enum(&cb, 2, delivery);
    if (with_batching) {   /* batch_spec.batch_period, as send_sensor_config_request (sensors.ssc 0x14ee68) always sets it */
        s2_pb_tag(&bb, 1, 0); s2_pb_varint(&bb, batch_period_us);
        s2_pb_bytes(&rb, 1, bs, bb.n);
    }
    if (payload) s2_pb_bytes(&rb, 2, payload, plen);
    if (sb.err || cb.err || rb.err || bb.err) return 0;
    s2_pb_bytes(&b, 1, s, sb.n);
    s2_pb_fixed32(&b, 2, msg_id);
    s2_pb_bytes(&b, 3, c, cb.n);
    s2_pb_bytes(&b, 4, r, rb.n);
    return done(&b);
}
size_t s2_qmi_request(uint8_t *out, size_t cap, uint16_t txn, const uint8_t *pb, size_t len)
{
    struct s2_buf b = { out, 0, cap, 0 };
    if (!pb || !len || len > S2_PAYLOAD_MAX) return 0;
    uint8_t h[7] = { 0, (uint8_t)txn, (uint8_t)(txn >> 8), S2_QMI_REQ, 0, 0, 0 };
    put(&b, h, 7);
    uint8_t t1[5] = { 0x01, (uint8_t)(len + 2), (uint8_t)((len + 2) >> 8), (uint8_t)len, (uint8_t)(len >> 8) };
    put(&b, t1, 5); put(&b, pb, len);
    uint8_t t2[4] = { 0x10, 1, 0, 1 };
    put(&b, t2, 4);
    if (b.err) return 0;
    size_t body = b.n - 7; out[5] = (uint8_t)body; out[6] = (uint8_t)(body >> 8);
    return b.n;
}

static uint16_t g16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t g32(const uint8_t *p) { return g16(p) | (uint32_t)g16(p + 2) << 16; }
static uint64_t g64(const uint8_t *p) { return g32(p) | (uint64_t)g32(p + 4) << 32; }

int s2_qmi_decode_response(const uint8_t *b, size_t n, uint16_t txn, struct s2_qmi_resp *o)
{
    unsigned seen = 0;
    memset(o, 0, sizeof(*o));
    if (n < 7 || b[0] != 2 || g16(b + 1) != txn || g16(b + 3) != S2_QMI_REQ || g16(b + 5) != n - 7) return -1;
    for (size_t p = 7; p < n;) {
        if (n - p < 3) return -1;
        unsigned type = b[p], len = g16(b + p + 1); p += 3;
        if (len > n - p) return -1;
        unsigned bit = 0;
        if (type == 0x02) { if (len != 4) return -1; o->result = g16(b + p); o->error = g16(b + p + 2); bit = 1; }
        else if (type == 0x10) { if (len != 8) return -1; o->client_id = g64(b + p); o->has_client_id = 1; bit = 2; }
        else if (type == 0x11) { if (len != 4) return -1; o->sns_result = g32(b + p); o->has_result = 1; bit = 4; }
        if (seen & bit) return -1;
        seen |= bit; p += len;
    }
    return (seen & 1) ? 0 : -1;
}
int s2_qmi_decode_indication(const uint8_t *b, size_t n, uint64_t *cid, const uint8_t **pl, size_t *plen)
{
    unsigned seen = 0;
    if (n < 7 || b[0] != 4 || g16(b + 5) != n - 7) return -1;
    unsigned id = g16(b + 3);
    if (id != S2_QMI_REPORT_IND && id != S2_QMI_JUMBO_IND) return -1;
    size_t max = id == S2_QMI_REPORT_IND ? S2_PAYLOAD_MAX : S2_JUMBO_MAX;
    for (size_t p = 7; p < n;) {
        if (n - p < 3) return -1;
        unsigned type = b[p], len = g16(b + p + 1); p += 3;
        if (len > n - p) return -1;
        if (type == 0x01) { if (len != 8 || (seen & 1)) return -1; *cid = g64(b + p); seen |= 1; }
        else if (type == 0x02) {
            if (len < 2 || (seen & 2)) return -1;
            size_t a = g16(b + p);
            if (a != len - 2u || a > max) return -1;
            *pl = b + p + 2; *plen = a; seen |= 2;
        }
        p += len;
    }
    return seen == 3 ? 0 : -1;
}

/* protobuf reader */
struct rd { const uint8_t *p; size_t n, i; };
static int rvar(struct rd *r, uint64_t *v)
{
    uint64_t x = 0;
    for (unsigned s = 0; s < 64; s += 7) {
        if (r->i >= r->n) return -1;
        uint8_t c = r->p[r->i++];
        x |= (uint64_t)(c & 0x7f) << s;
        if (!(c & 0x80)) { *v = x; return 0; }
    }
    return -1;
}
/* next field: 0 ok, 1 end, -1 malformed. For wire 2, d and len point to the bytes. */
static int rnext(struct rd *r, unsigned *field, unsigned *wire, uint64_t *v, const uint8_t **d, size_t *len)
{
    uint64_t key;
    if (r->i == r->n) return 1;
    if (rvar(r, &key) || !(key >> 3) || (key >> 3) > 0x1fffffff) return -1;
    *field = (unsigned)(key >> 3); *wire = key & 7;
    switch (*wire) {
    case 0: return rvar(r, v);
    case 1: if (r->n - r->i < 8) return -1; *v = g64(r->p + r->i); r->i += 8; return 0;
    case 5: if (r->n - r->i < 4) return -1; *v = g32(r->p + r->i); r->i += 4; return 0;
    case 2: {
        uint64_t l; if (rvar(r, &l) || l > r->n - r->i) return -1;
        *d = r->p + r->i; *len = (size_t)l; r->i += (size_t)l; return 0;
    }
    default: return -1;
    }
}
static float f32(uint64_t v) { uint32_t u = (uint32_t)v; float f; memcpy(&f, &u, 4); return f; }
void s2_sanitize(char *out, size_t cap, const uint8_t *in, size_t len)
{
    size_t o = 0;
    if (!cap) return;
    for (size_t i = 0; i < len && o + 1 < cap; i++) {
        uint8_t c = in[i];
        out[o++] = (c >= 0x20 && c <= 0x7e && c != '"' && c != '\\') ? (char)c : '?';
    }
    out[o] = 0;
}
static int decode_suid(const uint8_t *d, size_t n, struct s2_suid *s)
{
    struct rd r = { d, n, 0 }; unsigned f, w, seen = 0; uint64_t v = 0; const uint8_t *x; size_t l; int rc;
    while (!(rc = rnext(&r, &f, &w, &v, &x, &l))) {
        if (f == 1 && w == 1) { s->low = v; seen |= 1; }
        else if (f == 2 && w == 1) { s->high = v; seen |= 2; }
    }
    return rc < 0 || seen != 3 ? -1 : 0;
}
int s2_decode_event_msg(const uint8_t *b, size_t n, struct s2_event_msg *o)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc;
    memset(o, 0, sizeof(*o));
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) {
        if (f == 1 && w == 2) { if (decode_suid(d, l, &o->suid)) return -1; o->has_suid = 1; }
        else if (f == 2 && w == 2) {
            if (o->n == S2_MAX_EVENTS) return -1;
            struct s2_event *e = &o->ev[o->n]; unsigned seen = 0;
            struct rd q = { d, l, 0 }; unsigned g, x; uint64_t u = 0; const uint8_t *pd; size_t pl; int qc;
            memset(e, 0, sizeof(*e));
            while (!(qc = rnext(&q, &g, &x, &u, &pd, &pl))) {
                if (g == 1 && x == 5) { e->msg_id = (uint32_t)u; seen |= 1; }
                else if (g == 2 && x == 1) { e->timestamp = u; seen |= 2; }
                else if (g == 3 && x == 2) { e->payload = pd; e->payload_len = pl; seen |= 4; }
            }
            if (qc < 0 || (seen & 3) != 3) return -1;
            o->n++;
        }
    }
    return rc < 0 || !o->has_suid ? -1 : 0;
}
int s2_decode_suid_event(const uint8_t *b, size_t n, struct s2_suid_event *o)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc, has_dt = 0;
    memset(o, 0, sizeof(*o));
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) {
        if (f == 1 && w == 2) { s2_sanitize(o->data_type, sizeof(o->data_type), d, l); has_dt = 1; }
        else if (f == 2 && w == 2) {
            if (o->n == S2_MAX_SUIDS) return -1;
            if (decode_suid(d, l, &o->suid[o->n])) return -1;
            o->n++;
        }
    }
    return rc < 0 || !has_dt ? -1 : 0;
}
int s2_decode_sensor_event(const uint8_t *b, size_t n, struct s2_sensor_event *o)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc;
    memset(o, 0, sizeof(*o));
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) {
        if (f == 1 && w == 5) { if (o->n == S2_MAX_FLOATS) return -1; o->data[o->n++] = f32(v); }
        else if (f == 1 && w == 2) {   /* packed form, accepted too */
            if (l % 4) return -1;
            for (size_t i = 0; i < l; i += 4) { if (o->n == S2_MAX_FLOATS) return -1; o->data[o->n++] = f32(g32(d + i)); }
        } else if (f == 2 && w == 0) { o->status = (uint32_t)v; o->has_status = 1; }
    }
    return rc < 0 ? -1 : 0;
}
int s2_decode_proximity_event(const uint8_t *b, size_t n, int *near, uint32_t *status)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc;
    int found = 0;
    *near = 0; *status = 0;
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) {
        if (f == 1 && w == 0) { if (v > 1) return -1; *near = (int)v; found = 1; }
        else if (f == 3 && w == 0) { if (v > 3) return -1; *status = (uint32_t)v; }
    }
    return rc < 0 || !found ? -1 : 0;
}
static int decode_value(const uint8_t *d, size_t l, int id, struct s2_attrs *o)
{
    struct rd r = { d, l, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *x; size_t xl; int rc;
    while (!(rc = rnext(&r, &f, &w, &v, &x, &xl))) {
        if (f != 1 || w != 2) continue;            /* values (repeated sns_std_attr_value_data) */
        struct rd q = { x, xl, 0 }; unsigned g, y; uint64_t u = 0; const uint8_t *s; size_t sl; int qc;
        while (!(qc = rnext(&q, &g, &y, &u, &s, &sl))) {
            if (g == 2 && y == 2) {                 /* str */
                char *dst = id == S2_ATTR_NAME ? o->name : id == S2_ATTR_VENDOR ? o->vendor : id == S2_ATTR_TYPE ? o->type : NULL;
                /* F1: the SSC sends C strings with their terminator; stock drops exactly one trailing
                 * 0x00 before use (sensors.ssc.so, ssc_sensor::set_sensor_typename, 0x14b5b8; review
                 * 79k section 1). Drop one, never more, then sanitise. */
                if (sl && s[sl - 1] == 0) sl--;
                if (dst && !dst[0]) s2_sanitize(dst, 64, s, sl);
            } else if (g == 3 && y == 5 && id == S2_ATTR_RATES && o->n_rates < S2_MAX_FLOATS) {
                o->rates[o->n_rates++] = f32(u);
            } else if (g == 4 && y == 1 && id == S2_ATTR_STREAM_TYPE && !o->has_stream_type) {
                o->stream_type = (int64_t)u; o->has_stream_type = 1;
            } else if (g == 5 && y == 0 && id == S2_ATTR_AVAILABLE && !o->has_available) {
                o->available = u != 0; o->has_available = 1;
            }
        }
        if (qc < 0) return -1;
    }
    return rc < 0 ? -1 : 0;
}
int s2_decode_attr_event(const uint8_t *b, size_t n, struct s2_attrs *o)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc;
    memset(o, 0, sizeof(*o));
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) {
        if (f != 1 || w != 2) continue;
        struct rd q = { d, l, 0 }; unsigned g, y; uint64_t u = 0; const uint8_t *x; size_t xl; int qc;
        int id = -1; const uint8_t *val = NULL; size_t vl = 0;
        while (!(qc = rnext(&q, &g, &y, &u, &x, &xl))) {
            if (g == 1 && y == 0) id = (int)(int32_t)u;
            else if (g == 2 && y == 2) { val = x; vl = xl; }
        }
        if (qc < 0) return -1;
        if (id >= 0 && val && decode_value(val, vl, id, o)) return -1;
    }
    return rc < 0 ? -1 : 0;
}
int s2_decode_error_event(const uint8_t *b, size_t n, uint32_t *err)
{
    struct rd r = { b, n, 0 }; unsigned f, w; uint64_t v = 0; const uint8_t *d; size_t l; int rc, seen = 0;
    while (!(rc = rnext(&r, &f, &w, &v, &d, &l))) if (f == 1 && w == 0) { *err = (uint32_t)v; seen = 1; }
    return rc < 0 || !seen ? -1 : 0;
}

static const struct { const char *dt, *tokens[3]; const char *joined; } parts[] = {
    { "ambient_light", { "stk3b6x", NULL, NULL }, "stk3b6x" },
    { "mag",           { "ak0991", NULL, NULL }, "ak0991" },
    { "proximity",     { "stk3b6x", NULL, NULL }, "stk3b6x" },
    { "accel",         { "kxtj3", "stk8", NULL }, "kxtj3|stk8" },
};
static int contains_ci(const char *hay, const char *needle)
{
    size_t n = strlen(needle);
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < n && hay[i] && (hay[i] | 0x20) == (needle[i] | 0x20)) i++;
        if (i == n) return 1;
    }
    return 0;
}
const char *s2_part_tokens(const char *dt)
{
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); i++) if (!strcmp(parts[i].dt, dt)) return parts[i].joined;
    return NULL;
}
int s2_part_expected(const char *dt, const char *name)
{
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); i++) {
        if (strcmp(parts[i].dt, dt)) continue;
        if (!name || !name[0]) return 0;
        for (size_t k = 0; k < 3 && parts[i].tokens[k]; k++) if (contains_ci(name, parts[i].tokens[k])) return 1;
        return 0;
    }
    return -1;
}
