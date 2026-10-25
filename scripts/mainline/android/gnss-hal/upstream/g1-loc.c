/* SPDX-License-Identifier: Apache-2.0
 * G1 QMI LOC codec and single-session state machine. No I/O except through
 * struct g1_link and the bounded output stream. See g1-loc.h.
 */
#define _POSIX_C_SOURCE 200809L
#include "g1-loc.h"
#include <math.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>

/* ------------------------------------------------------------------ policy */
/* Ids and names from the stock id->name initialiser (0x15c7e4) aligned with the
 * IDL tables; see runs/gnss-g1-prep.md section 3. Not exhaustive: the allowlist
 * is the gate, this list documents why the obvious neighbours are refused. */
const struct g1_forbidden g1_forbidden_requests[] = {
    {0x0035, "INJECT_PREDICTED_ORBITS_DATA: assistance; cached in modem EFS"},
    {0x0039, "INJECT_POSITION: assistance; cached position"},
    {0x003a, "SET_ENGINE_LOCK: persistent GPS lock (NV)"},
    {0x003c, "SET_SBAS_CONFIG: persistent engine config"},
    {0x003e, "SET_NMEA_TYPES: persistent engine config"},
    {0x0040, "SET_LOW_POWER_MODE: engine config"},
    {0x0042, "SET_SERVER: persistent A-GNSS server (NV)"},
    {0x0044, "DELETE_ASSIST_DATA: erases aiding/calibration in EFS"},
    {0x0045, "SET_XTRA_T_SESSION_CONTROL: persistent XTRA config"},
    {0x004c, "SET_SPI_STATUS: sensor config"},
    {0x0054, "SET_PROTOCOL_CONFIG_PARAMETERS: persistent SUPL/LPP config (NV)"},
    {0x0056, "SET_SENSOR_CONTROL_CONFIG: persistent sensor config"},
    {0x0058, "SET_SENSOR_PROPERTIES: persistent sensor config"},
    {0x005a, "SET_SENSOR_PERFORMANCE_CONTROL_CONFIGURATION: persistent"},
    {0x005c, "INJECT_SUPL_CERTIFICATE: writes certificate store (EFS)"},
    {0x005d, "DELETE_SUPL_CERTIFICATE: deletes from certificate store (EFS)"},
    {0x005e, "SET_POSITION_ENGINE_CONFIG_PARAMETERS: persistent engine config"},
    {0x0063, "ADD_CIRCULAR_GEOFENCE: persistent geofence store"},
    {0x0064, "DELETE_GEOFENCE: geofence store"},
    {0x0066, "EDIT_GEOFENCE: geofence store"},
    {0x0073, "SET_GEOFENCE_ENGINE_CONFIG: persistent"},
    {0x0084, "SET_PREMIUM_SERVICES_CONFIG: persistent"},
    {0x0085, "SET_XTRA_VERSION_CHECK: persistent XTRA config"},
    {0x00a6, "DELETE_GNSS_SERVICE_DATA: erases aiding/calibration in EFS"},
    {0x00a7, "INJECT_XTRA_DATA: assistance; cached in modem EFS"},
    {0x00a8, "INJECT_XTRA_PCID: persistent XTRA identity"},
    {0x00aa, "SET_INTERNAL_STATUS_CONFIG: engine config"},
    {0x00b6, "SET_BLACKLIST_SV: persistent constellation config"},
    {0x00b8, "SET_CONSTELLATION_CONTROL: persistent constellation config"},
    {0x00d0, "SET_MIN_GPS_WEEK_NUMBER: persistent (name alignment low confidence)"},
    {0x00d2, "SET_PARAMETER: generic persistent parameter (name alignment low confidence)"},
    {0x00d5, "SET_MULTIBAND_CONFIG: persistent (name alignment low confidence)"},
};
const size_t g1_forbidden_count = sizeof(g1_forbidden_requests) / sizeof(g1_forbidden_requests[0]);

int g1_request_allowed(uint16_t id)
{
    /* 0xba registers this client in modem RAM; 0x3b only reads the lock. */
    return id == G1_MSG_REG_EVENTS || id == G1_MSG_START || id == G1_MSG_STOP || id == G1_MSG_SET_OPERATION_MODE ||
           id == G1_MSG_REGISTER_MASTER || id == G1_MSG_GET_ENGINE_LOCK || id == G1_MSG_INJECT_UTC_TIME;
}

/* ------------------------------------------------------------------- wire */
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t rd64(const uint8_t *p) { return (uint64_t)rd32(p) | (uint64_t)rd32(p + 4) << 32; }
static float rdf(const uint8_t *p) { uint32_t u = rd32(p); float f; memcpy(&f, &u, 4); return f; }
static double rdd(const uint8_t *p) { uint64_t u = rd64(p); double d; memcpy(&d, &u, 8); return d; }
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }

int g1_parse(const uint8_t *b, size_t n, struct g1_msg *m)
{
    memset(m, 0, sizeof(*m));
    if (!b || n < G1_QMI_HEADER || n > G1_FRAME_MAX) return -1;
    m->type = b[0]; m->txn = rd16(b + 1); m->id = rd16(b + 3); m->len = rd16(b + 5);
    if (m->type != G1_QMI_REQUEST && m->type != G1_QMI_RESPONSE && m->type != G1_QMI_INDICATION) return -1;
    if ((size_t)m->len != n - G1_QMI_HEADER) return -1;
    size_t at = G1_QMI_HEADER;
    while (at < n) {
        if (n - at < 3 || m->ntlv == G1_MAX_TLVS) return -1;
        struct g1_tlv t = { b[at], rd16(b + at + 1), b + at + 3 };
        if ((size_t)t.len > n - at - 3) return -1;
        for (unsigned i = 0; i < m->ntlv; i++)
            if (m->tlv[i].type == t.type) return -1;   /* duplicate TLV: conflicting values */
        m->tlv[m->ntlv++] = t;
        at += 3 + (size_t)t.len;
    }
    return 0;
}

const struct g1_tlv *g1_find(const struct g1_msg *m, uint8_t type)
{
    for (unsigned i = 0; i < m->ntlv; i++)
        if (m->tlv[i].type == type) return &m->tlv[i];
    return NULL;
}

size_t g1_encode_request(uint8_t *b, size_t cap, uint16_t txn, uint16_t id, const uint8_t *tlvs, size_t tlv_len)
{
    if (!g1_request_allowed(id) || !txn || tlv_len > G1_MAX_MSG_LEN || cap < G1_QMI_HEADER + tlv_len) return 0;
    b[0] = G1_QMI_REQUEST; wr16(b + 1, txn); wr16(b + 3, id); wr16(b + 5, (uint16_t)tlv_len);
    memcpy(b + G1_QMI_HEADER, tlvs, tlv_len);
    /* Self-check: the frame we send must parse strictly. */
    struct g1_msg chk;
    if (g1_parse(b, G1_QMI_HEADER + tlv_len, &chk)) return 0;
    return G1_QMI_HEADER + tlv_len;
}

static size_t put_tlv(uint8_t *p, uint8_t type, const void *v, uint16_t len)
{
    p[0] = type; wr16(p + 1, len); memcpy(p + 3, v, len); return 3u + len;
}
static void le32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }

size_t g1_encode_reg_events(uint8_t *b, size_t cap, uint16_t txn)
{
    /* Stock locClientRegisterEventMask (0x160e4c): 0x01 mask, 0x10 clientStrId
     * (raw, no NUL; "MHAL" when master), 0x11 clientType 1, 0x12
     * enablePosRequestNotification 0. Trial 1 sent only 0x01. */
    uint8_t t[48], v[8]; uint64_t mask = G1_EVENT_MASK; uint8_t zero = 0; size_t n = 0;
    for (int i = 0; i < 8; i++) v[i] = (uint8_t)(mask >> (8 * i));
    n += put_tlv(t + n, 0x01, v, 8);
    n += put_tlv(t + n, 0x10, G1_CLIENT_STR, (uint16_t)(sizeof(G1_CLIENT_STR) - 1));
    le32(v, G1_CLIENT_TYPE); n += put_tlv(t + n, 0x11, v, 4);
    n += put_tlv(t + n, 0x12, &zero, 1);
    return g1_encode_request(b, cap, txn, G1_MSG_REG_EVENTS, t, n);
}

size_t g1_encode_time(uint8_t *b, size_t cap, uint16_t txn, uint64_t utc_ms, uint32_t uncertainty_ms)
{
    if (utc_ms < UINT64_C(1577836800000) || utc_ms > UINT64_C(4102444800000) || uncertainty_ms > 86404320u) return 0;
    uint8_t t[18], v[8];
    for (int i = 0; i < 8; i++) v[i] = (uint8_t)(utc_ms >> (8*i));
    size_t n = put_tlv(t, 0x01, v, 8);
    le32(v, uncertainty_ms); n += put_tlv(t+n, 0x02, v, 4);
    return g1_encode_request(b, cap, txn, G1_MSG_INJECT_UTC_TIME, t, n);
}

size_t g1_encode_register_master(uint8_t *b, size_t cap, uint16_t txn)
{
    uint8_t t[8], v[4]; le32(v, G1_MASTER_KEY);
    size_t n = put_tlv(t, 0x01, v, 4);
    return g1_encode_request(b, cap, txn, G1_MSG_REGISTER_MASTER, t, n);
}

size_t g1_encode_get_engine_lock(uint8_t *b, size_t cap, uint16_t txn)
{
    /* No optional subType: the plain query (IDL: 0x10 optional). */
    return g1_encode_request(b, cap, txn, G1_MSG_GET_ENGINE_LOCK, (const uint8_t *)"", 0);
}

size_t g1_encode_set_mode(uint8_t *b, size_t cap, uint16_t txn)
{
    uint8_t t[8], v[4]; le32(v, G1_MODE_STANDALONE);
    size_t n = put_tlv(t, 0x01, v, 4);
    return g1_encode_request(b, cap, txn, G1_MSG_SET_OPERATION_MODE, t, n);
}

size_t g1_encode_start(uint8_t *b, size_t cap, uint16_t txn)
{
    /* TLV order and values mirror stock startFix (0x157cbc) for a 1 Hz periodic,
     * high-accuracy request: 0x01 sessionId, 0x10 fixRecurrence, 0x11
     * horizontalAccuracyLevel, 0x13 minInterval, 0x15 configAltitudeAssumption.
     * Omitted: 0x12 intermediate (stock omits), 0x14 applicationId, 0x18
     * sharePosition, 0x1a powerMode, 0x16/0x17 (low-accuracy only). */
    uint8_t t[64], v[4]; size_t n = 0; uint8_t sid = G1_SESSION_ID;
    n += put_tlv(t + n, 0x01, &sid, 1);
    le32(v, G1_RECURRENCE_PERIODIC); n += put_tlv(t + n, 0x10, v, 4);
    le32(v, G1_ACCURACY_HIGH); n += put_tlv(t + n, 0x11, v, 4);
    le32(v, G1_MIN_INTERVAL_MS); n += put_tlv(t + n, 0x13, v, 4);
    le32(v, G1_ALTITUDE_ASSUMPTION_DISABLED); n += put_tlv(t + n, 0x15, v, 4);
    uint8_t share = G1_SHARE_POSITION; n += put_tlv(t + n, 0x18, &share, 1);
    return g1_encode_request(b, cap, txn, G1_MSG_START, t, n);
}

size_t g1_encode_stop(uint8_t *b, size_t cap, uint16_t txn)
{
    uint8_t t[8], sid = G1_SESSION_ID;
    size_t n = put_tlv(t, 0x01, &sid, 1);
    return g1_encode_request(b, cap, txn, G1_MSG_STOP, t, n);
}

int g1_response_result(const struct g1_msg *m, uint16_t *error)
{
    const struct g1_tlv *t = g1_find(m, 0x02);
    if (!t || t->len != 4) return -1;
    uint16_t result = rd16(t->v), err = rd16(t->v + 2);
    if (error) *error = err;
    if (result == 0 && err == 0) return 0;
    if (result == 1) return 1;
    return -1;
}

/* ----------------------------------------------------------- strict specs */
enum { K_FIXED, K_VAR, K_STRING };
struct spec { uint8_t type, kind; uint16_t size; uint16_t max; };
/* Wire sizes from the stock IDL (loc-idl-walk.py): var arrays carry a 1-byte
 * count (every max here is <= 255), whole-TLV strings are raw bytes. */
static const struct spec POS[] = {
    {0x01,K_FIXED,4,0},{0x02,K_FIXED,1,0},{0x10,K_FIXED,8,0},{0x11,K_FIXED,8,0},{0x12,K_FIXED,4,0},
    {0x13,K_FIXED,4,0},{0x14,K_FIXED,4,0},{0x15,K_FIXED,4,0},{0x16,K_FIXED,1,0},{0x17,K_FIXED,4,0},
    {0x18,K_FIXED,4,0},{0x19,K_FIXED,4,0},{0x1a,K_FIXED,4,0},{0x1b,K_FIXED,4,0},{0x1c,K_FIXED,4,0},
    {0x1d,K_FIXED,1,0},{0x1e,K_FIXED,4,0},{0x1f,K_FIXED,4,0},{0x20,K_FIXED,4,0},{0x21,K_FIXED,4,0},
    {0x22,K_FIXED,4,0},{0x23,K_FIXED,4,0},{0x24,K_FIXED,12,0},{0x25,K_FIXED,8,0},{0x26,K_FIXED,1,0},
    {0x27,K_FIXED,6,0},{0x28,K_FIXED,4,0},{0x29,K_FIXED,4,0},{0x2a,K_FIXED,8,0},{0x2b,K_FIXED,4,0},
    {0x2c,K_VAR,2,80},{0x2d,K_FIXED,1,0},{0x2e,K_FIXED,12,0},{0x2f,K_FIXED,12,0},{0x30,K_FIXED,8,0},
    {0x31,K_FIXED,4,0},{0x32,K_FIXED,1,0},{0x33,K_FIXED,20,0},{0x34,K_VAR,2,3},{0x35,K_FIXED,8,0},
    {0x36,K_VAR,2,176},{0x37,K_VAR,8,176},{0x38,K_VAR,8,19},{0x39,K_FIXED,4,0},{0x3a,K_FIXED,4,0},
    {0x3b,K_FIXED,8,0},{0x3c,K_FIXED,2,0},{0x3d,K_FIXED,4,0},{0x3e,K_FIXED,4,0},{0x3f,K_FIXED,8,0},
    {0x40,K_FIXED,4,0},{0x41,K_FIXED,4,0},{0x42,K_FIXED,4,0},
};
static const struct spec SV[] = {
    {0x01,K_FIXED,1,0},{0x10,K_VAR,28,80},{0x11,K_VAR,29,176},{0x12,K_VAR,8,176},{0x13,K_VAR,4,176},
};
static const struct spec NMEA[] = { {0x01,K_STRING,0,200},{0x10,K_STRING,0,4095} };
static const struct spec U32IND[] = { {0x01,K_FIXED,4,0} };
static const struct spec SESSION[] = { {0x01,K_FIXED,4,0},{0x10,K_FIXED,1,0} };
static const struct spec LOCK[] = { {0x01,K_FIXED,4,0},{0x10,K_FIXED,4,0},{0x11,K_FIXED,4,0},{0x12,K_FIXED,8,0} };

static int last_reason; static uint8_t last_tlv;
static int fail(int reason, uint8_t tlv) { last_reason = reason; last_tlv = tlv; return -1; }
int g1_last_reject(uint8_t *tlv) { if (tlv) *tlv = last_tlv; return last_reason; }
const char *g1_reason_name(int r)
{
    static const char *n[] = {"none", "not-indication", "wrong-id", "unknown-mandatory", "fixed-size", "count-missing",
                              "count-over-max", "array-length", "string-length", "string-char", "missing-mandatory", "semantic"};
    return r >= 0 && r <= G1R_SEMANTIC ? n[r] : "invalid";
}

/* Validates every TLV of an indication against its spec list. Mandatory TLVs
 * (< 0x10) must all be present; unknown mandatory TLVs are rejected; unknown
 * optional TLVs are counted (a stock-matched modem should send none). */
static int check(const struct g1_msg *m, const struct spec *s, size_t ns, uint8_t mand_hi, unsigned *unknown)
{
    last_reason = G1R_NONE; last_tlv = 0;
    if (m->type != G1_QMI_INDICATION) return fail(G1R_NOT_INDICATION, 0);
    *unknown = 0;
    for (unsigned i = 0; i < m->ntlv; i++) {
        const struct g1_tlv *t = &m->tlv[i]; const struct spec *p = NULL;
        for (size_t k = 0; k < ns; k++) if (s[k].type == t->type) p = &s[k];
        if (!p) { if (t->type < 0x10) return fail(G1R_UNKNOWN_MANDATORY, t->type); (*unknown)++; continue; }
        if (p->kind == K_FIXED && t->len != p->size) return fail(G1R_FIXED_SIZE, t->type);
        if (p->kind == K_VAR) {
            if (t->len < 1) return fail(G1R_COUNT_MISSING, t->type);
            if (t->v[0] > p->max) return fail(G1R_COUNT_OVER_MAX, t->type);
            if (t->len != 1u + (unsigned)t->v[0] * p->size) return fail(G1R_ARRAY_LENGTH, t->type);
        }
        if (p->kind == K_STRING) {
            size_t l = t->len;
            if (l && t->v[l - 1] == 0) l--;                    /* tolerate one terminating NUL */
            if (l > p->max) return fail(G1R_STRING_LENGTH, t->type);
            for (size_t k = 0; k < l; k++) if (t->v[k] < 0x0a || t->v[k] > 0x7e) return fail(G1R_STRING_CHAR, t->type);
        }
    }
    for (uint8_t mt = 1; mt <= mand_hi; mt++) if (!g1_find(m, mt)) return fail(G1R_MISSING_MANDATORY, mt);
    return 0;
}

int g1_decode_position(const struct g1_msg *m, struct g1_fix *f)
{
    memset(f, 0, sizeof(*f));
    if (m->id != G1_IND_POSITION) return fail(G1R_WRONG_ID, 0);
    if (check(m, POS, sizeof(POS) / sizeof(POS[0]), 2, &f->unknown_tlvs)) return -1;
    f->status = rd32(g1_find(m, 0x01)->v);
    f->session = g1_find(m, 0x02)->v[0];
    const struct g1_tlv *la = g1_find(m, 0x10), *lo = g1_find(m, 0x11), *h = g1_find(m, 0x12), *te = g1_find(m, 0x23),
                        *ut = g1_find(m, 0x25), *us = g1_find(m, 0x2c);
    if (la && lo) { f->has_latlon = 1; f->lat = rdd(la->v); f->lon = rdd(lo->v); }
    else if (la || lo) return fail(G1R_SEMANTIC, la ? 0x11 : 0x10);
    if (h) { f->has_hunc = 1; f->hunc = rdf(h->v); }
    if (te) { f->has_tech = 1; f->tech = rd32(te->v); }
    if (ut) { f->has_utc = 1; f->utc_ms = rd64(ut->v); }
    if (us) { f->has_used = 1; f->used = us->v[0]; }
    return 0;
}

int g1_decode_sv(const struct g1_msg *m, struct g1_sv_report *r)
{
    memset(r, 0, sizeof(*r));
    if (m->id != G1_IND_SV_INFO) return fail(G1R_WRONG_ID, 0);
    if (check(m, SV, sizeof(SV) / sizeof(SV[0]), 1, &r->unknown_tlvs)) return -1;
    r->alt_assumed = g1_find(m, 0x01)->v[0];
    const struct g1_tlv *a = g1_find(m, 0x10), *x = g1_find(m, 0x11), *sg = g1_find(m, 0x12), *rf = g1_find(m, 0x13);
    if (a) { r->has_legacy = 1; r->legacy_n = a->v[0]; }
    if (x) { r->has_expanded = 1; r->expanded_n = x->v[0]; }
    if (sg) { r->has_sig = 1; r->sig_n = sg->v[0]; }
    if (rf) { r->has_rf = 1; r->rf_n = rf->v[0]; }
    /* Stock reportSv (0x13476c): expandedSvList_valid selects the expanded list
     * (even when it is empty); otherwise svList. Both lists may be present. */
    const struct g1_tlv *t = x ? x : a;
    if (!t) return 0;
    size_t step = t == x ? 29u : 28u;
    r->expanded = t == x;
    for (unsigned i = 0; i < t->v[0]; i++) {
        const uint8_t *p = t->v + 1 + i * step; struct g1_sv *s = &r->sv[r->n++];
        s->valid = rd32(p); s->system = rd32(p + 4); s->id = rd16(p + 8); s->health = p[10];
        s->status = rd32(p + 11); s->info = p[15]; s->elev = rdf(p + 16); s->azim = rdf(p + 20); s->cn0 = rdf(p + 24);
    }
    return 0;
}

int g1_decode_nmea(const struct g1_msg *m, struct g1_nmea_report *r)
{
    memset(r, 0, sizeof(*r));
    if (m->id != G1_IND_NMEA) return fail(G1R_WRONG_ID, 0);
    if (check(m, NMEA, 2, 1, &r->unknown_tlvs)) return -1;
    const struct g1_tlv *t = g1_find(m, 0x10);
    if (!t || !t->len || (t->len == 1 && !t->v[0])) t = g1_find(m, 0x01);
    size_t l = t->len; if (l && t->v[l - 1] == 0) l--;
    size_t i = 0;
    while (i < l) {
        while (i < l && (t->v[i] == '\r' || t->v[i] == '\n')) i++;
        if (i >= l) break;
        size_t s = i;
        while (i < l && t->v[i] != '\r' && t->v[i] != '\n') i++;
        if (i - s < 6 || t->v[s] != '$') { r->other++; r->sentences++; continue; }
        const char *k = (const char *)t->v + s + 3;   /* talker-independent type */
        if (!memcmp(k, "GSV", 3)) r->gsv++; else if (!memcmp(k, "GSA", 3)) r->gsa++;
        else if (!memcmp(k, "GGA", 3)) r->gga++; else if (!memcmp(k, "RMC", 3)) r->rmc++; else r->other++;
        r->sentences++;
    }
    return 0;
}

int g1_decode_u32_status(const struct g1_msg *m, uint32_t *v)
{
    unsigned u;
    if (check(m, U32IND, 1, 1, &u) || u) return -1;
    *v = rd32(g1_find(m, 0x01)->v);
    return 0;
}

int g1_decode_session_state(const struct g1_msg *m, uint32_t *state, int *has_id, uint8_t *id)
{
    unsigned u;
    if (m->id != G1_IND_FIX_SESSION_STATE || check(m, SESSION, 2, 1, &u) || u) return -1;
    *state = rd32(g1_find(m, 0x01)->v);
    const struct g1_tlv *t = g1_find(m, 0x10);
    *has_id = t != NULL; *id = t ? t->v[0] : 0;
    return 0;
}

int g1_decode_engine_lock(const struct g1_msg *m, struct g1_lock *l)
{
    unsigned u;
    memset(l, 0, sizeof(*l));
    if (m->id != G1_MSG_GET_ENGINE_LOCK || check(m, LOCK, 4, 1, &u) || u) return -1;
    l->status = rd32(g1_find(m, 0x01)->v);
    const struct g1_tlv *a = g1_find(m, 0x10), *b = g1_find(m, 0x11), *c = g1_find(m, 0x12);
    if (a) { l->has_type = 1; l->type = rd32(a->v); }
    if (b) { l->has_sub = 1; l->sub = rd32(b->v); }
    if (c) { l->has_client = 1; l->client = rd64(c->v); }
    return 0;
}

static const struct spec *spec_for(uint16_t id, size_t *n)
{
    switch (id) {
    case G1_IND_POSITION: *n = sizeof(POS) / sizeof(POS[0]); return POS;
    case G1_IND_SV_INFO: *n = sizeof(SV) / sizeof(SV[0]); return SV;
    default: *n = 0; return NULL;
    }
}

size_t g1_tlv_summary(const struct g1_msg *m, char *buf, size_t cap)
{
    size_t ns, at = 0; const struct spec *sp = spec_for(m->id, &ns);
    if (!cap) return 0;
    buf[0] = 0;
    for (unsigned i = 0; i < m->ntlv && i < 24; i++) {
        const struct g1_tlv *t = &m->tlv[i]; int var = 0;
        for (size_t k = 0; k < ns; k++) if (sp[k].type == t->type && sp[k].kind == K_VAR) var = 1;
        char one[32];
        int n = var && t->len ? snprintf(one, sizeof(one), "%s%02x:%u/c%u", i ? "," : "", t->type, t->len, t->v[0])
                              : snprintf(one, sizeof(one), "%s%02x:%u", i ? "," : "", t->type, t->len);
        if (n < 0 || at + (size_t)n + 1 > cap) break;
        memcpy(buf + at, one, (size_t)n + 1); at += (size_t)n;
    }
    if (m->ntlv > 24 && at + 5 < cap) { memcpy(buf + at, ",...", 5); at += 4; }
    return at;
}

/* --------------------------------------------------------------- session */
const char *g1_level_name(int l)
{
    static const char *n[] = {"NONE", "SESSION_ONLY", "ENGINE_ON", "SV_SEEN", "SV_TRACKED", "FIX"};
    return l >= 0 && l <= G1_FIX ? n[l] : "INVALID";
}

struct run {
    const struct g1_link *L; FILE *out; size_t written; int capped;
    uint16_t txn; int64_t t0, end, sec_start; unsigned packets, sec_packets;
    int level, start_acked, engine_on, set_mode_ind, set_mode_status, master_ind, master_status, lock_ind;
    struct g1_lock lock; unsigned dup_responses; uint16_t done_txn[16]; uint16_t done_id[16]; unsigned ndone;
    unsigned pos_reports, pos_success, sv_reports, sv_max, sv_tracked_max, nmea_inds, unknown_tlvs, sessions_started, sessions_finished;
    float cn0_max; unsigned nmea_gsv, nmea_gga_rmc;
    int64_t first_sv_ms, first_track_ms, fix_ms; float fix_hunc; unsigned fix_used; uint32_t fix_tech;
    int64_t start_ack_ms; int fix_fd; unsigned fix_records, fix_write_errors;
    /* per-minute counts: element-wise max over the minute's SV reports */
    long minute; unsigned min_reports, min_sv_max, min_tracked_max, min_hist[G1_CN0_BUCKETS];
    uint32_t last_fail_status; unsigned fail_reports;
    unsigned time_sent; int64_t last_time;
    int sv_detail; unsigned detail_bytes;
    uint8_t rx[G1_RX_BUFFER];
};

static void emit(struct run *R, const char *fmt, ...)
{
    if (R->capped) return;
    char line[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line), fmt, ap); va_end(ap);
    if (n < 0) return;
    if ((size_t)n >= sizeof(line)) n = (int)sizeof(line) - 1;
    if (R->written + (size_t)n + 64 > G1_MAX_OUTPUT) {
        fputs("A9_GNSS_G1 output-capped\n", R->out); R->capped = 1; fflush(R->out); return;
    }
    fputs(line, R->out); R->written += (size_t)n; fflush(R->out);
}

/* Non-location runtime events share the already-private RAM descriptor. */
static int event(struct run *R, const char *fmt, unsigned value)
{
    if (R->fix_fd < 0) return 0;
    char line[80]; int n=snprintf(line,sizeof(line),fmt,value);
    return n > 0 && n < (int)sizeof(line) && write(R->fix_fd,line,(size_t)n)==n ? 0 : -1;
}

static int64_t now(struct run *R) { return R->L->now_ms(R->L->ctx); }

/* Raw bytes of control frames only (responses, mode/master/lock indications):
 * they carry status codes, never position data. Capped at 64 bytes. */
static void emit_hex(struct run *R, const char *what, const uint8_t *b, size_t n)
{
    char h[2 * 64 + 1]; size_t k = n < 64 ? n : 64;
    for (size_t i = 0; i < k; i++) snprintf(h + 2 * i, 3, "%02x", b[i]);
    h[2 * k] = 0;
    emit(R, "A9_GNSS_G1 frame %s len=%zu hex=%s\n", what, n, h);
}

/* Diagnosable rejects: reason, offending TLV and the TLV layout (types, lengths,
 * array count bytes). No record bytes, IDs, angles, signal values or text. */
static void emit_reject(struct run *R, const char *what, const struct g1_msg *m)
{
    uint8_t tlv = 0; int reason = g1_last_reject(&tlv); char sum[24 * 16];
    g1_tlv_summary(m, sum, sizeof(sum));
    emit(R, "A9_GNSS_G1 reject %s\n", what);
    emit(R, "A9_GNSS_G1 reject-detail what=%s id=0x%04x len=%u reason=%s tlv=0x%02x ntlv=%u tlvs=%s\n", what, m->id, m->len,
         g1_reason_name(reason), tlv, m->ntlv, sum);
}

static void raise_level(struct run *R, int l) { if (l > R->level) R->level = l; }

/* Valid standalone fix: fresh (after START ack), this session, SUCCESS, finite
 * in-range coordinates, finite positive uncertainty, satellite technology and
 * no cell/Wi-Fi/reference/injected-coarse contribution. Coordinates are never
 * printed. */
static int fix_valid(const struct g1_fix *f)
{
    return f->status == 0 && f->session == G1_SESSION_ID && f->has_latlon && isfinite(f->lat) && isfinite(f->lon) &&
           f->lat >= -90.0 && f->lat <= 90.0 && f->lon >= -180.0 && f->lon <= 180.0 && !(f->lat == 0.0 && f->lon == 0.0) &&
           f->has_hunc && isfinite(f->hunc) && f->hunc > 0.0f && f->hunc < 100000.0f &&
           f->has_tech && (f->tech & 0x1u) && !(f->tech & (0x2u | 0x4u | 0x10u | 0x20u));
}

/* C/N0 buckets (dB-Hz): no valid C/N0, <15, 15-19, 20-24, 25-29, 30-34, 35-39, >=40 */
static const char *const BUCKET[G1_CN0_BUCKETS] = {"none", "lt15", "15", "20", "25", "30", "35", "40"};
static unsigned bucket_of(const struct g1_sv *s)
{
    if (!((s->valid & 0x80u) && isfinite(s->cn0) && s->cn0 > 0.0f && s->cn0 < 70.0f)) return 0;
    if (s->cn0 < 15.0f) return 1;
    unsigned b = 2u + (unsigned)((s->cn0 - 15.0f) / 5.0f);
    return b > 7 ? 7 : b;
}
static void minute_flush(struct run *R)
{
    if (!R->min_reports) return;
    char h[160]; size_t at = 0;
    for (unsigned b = 0; b < G1_CN0_BUCKETS; b++) {
        int n = snprintf(h + at, sizeof(h) - at, "%s%s:%u", b ? "," : "", BUCKET[b], R->min_hist[b]);
        if (n < 0 || (size_t)n >= sizeof(h) - at) break;
        at += (size_t)n;
    }
    emit(R, "A9_GNSS_G1 minute m=%ld reports=%u sv_max=%u tracked_max=%u cn0_hist=%s\n", R->minute, R->min_reports,
         R->min_sv_max, R->min_tracked_max, h);
    R->min_reports = R->min_sv_max = R->min_tracked_max = 0;
    memset(R->min_hist, 0, sizeof(R->min_hist));
}
static void minute_account(struct run *R, int64_t t, const struct g1_sv_report *r, unsigned tracked)
{
    long m = (long)(t / 60000);
    if (m != R->minute) { minute_flush(R); R->minute = m; }
    unsigned h[G1_CN0_BUCKETS] = {0};
    for (unsigned i = 0; i < r->n; i++) h[bucket_of(&r->sv[i])]++;
    R->min_reports++;
    if (r->n > R->min_sv_max) R->min_sv_max = r->n;
    if (tracked > R->min_tracked_max) R->min_tracked_max = tracked;
    for (unsigned b = 0; b < G1_CN0_BUCKETS; b++) if (h[b] > R->min_hist[b]) R->min_hist[b] = h[b];
}

/* Returns 0 handled, -1 protocol violation (seals the attempt). */
static int on_indication(struct run *R, const struct g1_msg *m)
{
    int64_t t = now(R) - R->t0;
    switch (m->id) {
    case G1_IND_INJECT_TIME_REQUEST:
        /* Optional time-server TLVs are deliberately ignored; the framework
         * chooses its own NTP source. No mandatory payload in LOC v02. */
        for (unsigned i=0; i<m->ntlv; ++i) if (m->tlv[i].type < 0x10) return -1;
        emit(R, "A9_GNSS_G1 time-request t_ms=%lld\n", (long long)t);
        return event(R,"A9_TIME_REQUEST %u\n",0);
    case G1_MSG_INJECT_UTC_TIME: {
        uint32_t status;
        if (g1_decode_u32_status(m,&status)) { emit_reject(R,"time-result-malformed",m); return -1; }
        emit(R,"A9_GNSS_G1 time-result status=%u\n",status);
        return event(R,"A9_TIME_RESULT %u\n",status);
    }
    case G1_IND_POSITION: {
        struct g1_fix f;
        if (g1_decode_position(m, &f)) { emit_reject(R, "position-malformed", m); return -1; }
        R->unknown_tlvs += f.unknown_tlvs;
        if (!R->start_acked) { emit(R, "A9_GNSS_G1 ignore position-before-start status=%u\n", f.status); return 0; }
        if (f.session != G1_SESSION_ID) { emit(R, "A9_GNSS_G1 reject position-session=%u\n", f.session); return -1; }
        R->pos_reports++;
        /* Preserve the actual used-SV list, never infer use from tracking or
         * ephemeris. Expanded list, if present, supersedes legacy. */
        const struct g1_tlv *used=g1_find(m,0x36);
        if (!used) used=g1_find(m,0x2c);
        if (R->fix_fd >= 0) {
            unsigned count=f.status==0 && used ? used->v[0] : 0;
            char line[1400]; int n=snprintf(line,sizeof(line),"A9_USED %u",count);
            for (unsigned i=0;i<count;++i) n+=snprintf(line+n,sizeof(line)-(size_t)n," %u",rd16(used->v+1+2*i));
            line[n++]='\n'; if (write(R->fix_fd,line,(size_t)n)!=n) return -1;
        }
        if (f.status == 0 && fix_valid(&f)) {
            R->pos_success++;
            if (R->level < G1_FIX) {
                R->fix_ms = t; R->fix_hunc = f.hunc; R->fix_used = f.has_used ? f.used : 0; R->fix_tech = f.tech;
                emit(R, "A9_GNSS_G1 fix t_ms=%lld ttff_ms=%lld hunc_m=%.1f sv_used=%u tech=0x%x (coordinates only in the private fix file)\n",
                     (long long)t, (long long)(t - R->start_ack_ms), (double)f.hunc, R->fix_used, f.tech);
            }
            /* Coordinates go ONLY to the private pre-opened fd, never to the console. */
            if (R->fix_fd >= 0 && R->fix_records < G1_MAX_FIX_RECORDS) {
                char rec[256];
                int n = snprintf(rec, sizeof(rec), "A9_GNSS_G1_FIX t_ms=%lld lat=%.8f lon=%.8f hunc_m=%.2f sv_used=%u tech=0x%x utc_ms=%llu\n",
                                 (long long)t, f.lat, f.lon, (double)f.hunc, f.has_used ? f.used : 0u, f.tech,
                                 (unsigned long long)(f.has_utc ? f.utc_ms : 0));
                if (n > 0 && n < (int)sizeof(rec) && write(R->fix_fd, rec, (size_t)n) == n) R->fix_records++;
                else R->fix_write_errors++;
            }
            raise_level(R, G1_FIX);
        } else if (f.status == 0) {
            emit(R, "A9_GNSS_G1 reject fix-criteria t_ms=%lld latlon=%d hunc=%d tech=0x%x\n", (long long)t, f.has_latlon, f.has_hunc, f.has_tech ? f.tech : 0u);
        } else if (f.status != 1) {
            R->fail_reports++; R->last_fail_status = f.status;
            if (R->fail_reports <= 8) emit(R, "A9_GNSS_G1 position-status t_ms=%lld status=%u\n", (long long)t, f.status);
        }
        return 0;
    }
    case G1_IND_SV_INFO: {
        static struct g1_sv_report r;   /* 176 entries: keep off the stack */
        if (g1_decode_sv(m, &r)) { emit_reject(R, "sv-malformed", m); return -1; }
        R->unknown_tlvs += r.unknown_tlvs;
        R->sv_reports++;
        unsigned tracked = 0; float best = 0;
        for (unsigned i = 0; i < r.n; i++) {
            const struct g1_sv *s = &r.sv[i];
            int has_cn0 = (s->valid & 0x80u) && isfinite(s->cn0) && s->cn0 > 0.0f && s->cn0 < 70.0f;
            if (has_cn0 && s->cn0 > best) best = s->cn0;
            if ((s->valid & 0x08u) && s->status == 3u && has_cn0) tracked++;
        }
        if (r.n > R->sv_max) R->sv_max = r.n;
        if (tracked > R->sv_tracked_max) R->sv_tracked_max = tracked;
        if (best > R->cn0_max) R->cn0_max = best;
        int first_populated = r.n && !R->first_sv_ms;
        if (r.n && !R->first_sv_ms) { R->first_sv_ms = t ? t : 1; }
        if (tracked && !R->first_track_ms) { R->first_track_ms = t ? t : 1; }
        if (r.n) raise_level(R, G1_SV_SEEN);
        if (tracked) raise_level(R, G1_SV_TRACKED);
        /* Summaries: first 5 reports, then every 30th. Per-SV detail only in
         * reports that are printed (system/id/status/elev/azim/C/N0; no position). */
        if (R->sv_reports <= 5 || R->sv_reports % 30 == 0 || first_populated) {
            emit(R, "A9_GNSS_G1 sv t_ms=%lld n=%u tracked=%u cn0_max=%.1f expanded=%d alt_assumed=%u lists=legacy:%s%u,expanded:%s%u,sig:%s%u,rf:%s%u\n",
                 (long long)t, r.n, tracked, (double)best, r.expanded, r.alt_assumed, r.has_legacy ? "" : "-", r.legacy_n,
                 r.has_expanded ? "" : "-", r.expanded_n, r.has_sig ? "" : "-", r.sig_n, r.has_rf ? "" : "-", r.rf_n);
        }
        if (R->sv_detail && (R->sv_reports<=5 || R->sv_reports%30==0)) {
            /* Explicit CLI diagnostic only, 64 KiB budget leaves STOP and
             * summaries available. No angles, coordinates or NMEA content. */
            for (unsigned i=0;i<r.n && R->detail_bytes+128<=65536;++i) {
                const struct g1_sv *s=&r.sv[i]; unsigned has=bucket_of(s)!=0;
                emit(R,"A9_GNSS_G1 sv-detail report=%u system=%u id=%u valid=0x%x cn0=%.2f\n",
                     R->sv_reports,s->system,s->id,s->valid,has?(double)s->cn0:0.);
                R->detail_bytes+=128;
            }
        }
        minute_account(R, t, &r, tracked);
        return 0;
    }
    case G1_IND_NMEA: {
        struct g1_nmea_report r;
        if (g1_decode_nmea(m, &r)) { emit_reject(R, "nmea-malformed", m); return -1; }
        R->unknown_tlvs += r.unknown_tlvs;
        R->nmea_inds++; R->nmea_gsv += r.gsv; R->nmea_gga_rmc += r.gga + r.rmc;
        return 0;   /* content is never printed (GGA/RMC carry position) */
    }
    case G1_IND_ENGINE_STATE: {
        uint32_t v;
        if (g1_decode_u32_status(m, &v) || ((v != 1 && v != 2) && fail(G1R_SEMANTIC, 0x01))) { emit_reject(R, "engine-state-malformed", m); return -1; }
        R->engine_on = v == 1;
        if (event(R,"A9_ENGINE %u\n",v)) return -1;
        emit(R, "A9_GNSS_G1 engine t_ms=%lld state=%s\n", (long long)t, v == 1 ? "on" : "off");
        if (v == 1) raise_level(R, G1_ENGINE_ON);
        return 0;
    }
    case G1_IND_FIX_SESSION_STATE: {
        uint32_t st; int has; uint8_t id;
        if (g1_decode_session_state(m, &st, &has, &id) || (((st != 1 && st != 2) || (has && id != G1_SESSION_ID)) && fail(G1R_SEMANTIC, 0x01))) {
            emit_reject(R, "session-state-malformed", m); return -1;
        }
        if (event(R,"A9_SESSION %u\n",st)) return -1;
        if (st == 1) R->sessions_started++; else R->sessions_finished++;
        emit(R, "A9_GNSS_G1 session-state t_ms=%lld state=%s\n", (long long)t, st == 1 ? "started" : "finished");
        return 0;
    }
    case G1_MSG_SET_OPERATION_MODE: {
        uint32_t v;
        if (g1_decode_u32_status(m, &v)) { emit_reject(R, "mode-ind-malformed", m); return -1; }
        R->set_mode_ind = 1; R->set_mode_status = (int)v;
        return 0;
    }
    case G1_MSG_REGISTER_MASTER: {
        uint32_t v;
        if (g1_decode_u32_status(m, &v)) { emit_reject(R, "master-ind-malformed", m); return -1; }
        R->master_ind = 1; R->master_status = (int)v;
        emit(R, "A9_GNSS_G1 master-indication status=%u\n", v);
        return 0;
    }
    case G1_MSG_GET_ENGINE_LOCK: {
        if (g1_decode_engine_lock(m, &R->lock)) { emit_reject(R, "lock-ind-malformed", m); return -1; }
        R->lock_ind = 1;
        emit(R, "A9_GNSS_G1 engine-lock status=%u type=%s%u sub=%s%u client=%s0x%llx (1 none, 2 MI, 3 MT, 4 all)\n", R->lock.status,
             R->lock.has_type ? "" : "absent:", R->lock.type, R->lock.has_sub ? "" : "absent:", R->lock.sub,
             R->lock.has_client ? "" : "absent:", (unsigned long long)R->lock.client);
        return 0;
    }
    default:
        /* Not subscribed: an unexpected indication id is a protocol violation. */
        emit(R, "A9_GNSS_G1 reject unexpected-indication id=0x%04x\n", m->id);
        return -1;
    }
}

/* One receive with flood accounting. 1 frame (m filled), 0 timeout, <0 fatal. */
static int receive(struct run *R, int64_t until, struct g1_msg *m)
{
    int64_t n0 = now(R);
    if (n0 < 0) { emit(R, "A9_GNSS_G1 fault clock\n"); return -3; }
    if (n0 >= until) return 0;
    int64_t w = until - n0; if (w > 1000) w = 1000;
    int r = R->L->recv(R->L->ctx, R->rx, sizeof(R->rx), (int)w);
    if (r == 0) return 0;
    if (r == -2) { emit(R, "A9_GNSS_G1 fault endpoint-lost\n"); return -2; }
    if (r < 0) { emit(R, "A9_GNSS_G1 fault transport\n"); return -1; }
    int64_t n1 = now(R);
    if (n1 < 0) { emit(R, "A9_GNSS_G1 fault clock\n"); return -3; }
    if (n1 - R->sec_start >= 1000) { R->sec_start = n1; R->sec_packets = 0; }
    if (++R->packets > G1_MAX_PACKETS || ++R->sec_packets > G1_MAX_PACKETS_PER_S) { emit(R, "A9_GNSS_G1 fault flood\n"); return -1; }
    if (g1_parse(R->rx, (size_t)r, m)) {
        emit(R, "A9_GNSS_G1 reject frame-malformed len=%d\n", r);
        /* QMI header only (type, txn, id, declared length); never the payload. */
        if (r >= (int)G1_QMI_HEADER) emit_hex(R, "malformed-header", R->rx, G1_QMI_HEADER);
        return -1;
    }
    return 1;
}

/* Sends one request and waits (bounded) for its response, dispatching
 * indications meanwhile. 0 success, 1 rejected by modem, <0 fatal. */
static int request_frame(struct run *R, const char *what, const uint8_t *b, size_t n, uint16_t txn, uint16_t id, int64_t limit)
{
    struct g1_msg m;
    if (!n) { emit(R, "A9_GNSS_G1 fault encode %s\n", what); return -1; }
    if (R->L->send(R->L->ctx, b, n)) { emit(R, "A9_GNSS_G1 fault send %s\n", what); return -1; }
    int64_t until = now(R) + G1_RESPONSE_MS;
    if (until > limit) until = limit;
    for (;;) {
        int r = receive(R, until, &m);
        if (r == 0) { if (now(R) >= until) { emit(R, "A9_GNSS_G1 fault response-timeout %s\n", what); return -1; } continue; }
        if (r < 0) return r;
        if (m.type == G1_QMI_INDICATION) {
            if (m.id == G1_MSG_SET_OPERATION_MODE || m.id == G1_MSG_REGISTER_MASTER || m.id == G1_MSG_GET_ENGINE_LOCK)
                emit_hex(R, "ind", R->rx, (size_t)m.len + G1_QMI_HEADER);
            if (on_indication(R, &m)) return -1;
            continue;
        }
        if (m.type == G1_QMI_RESPONSE) emit_hex(R, "resp", R->rx, (size_t)m.len + G1_QMI_HEADER);
        if (m.type == G1_QMI_RESPONSE && (m.txn != txn || m.id != id)) {
            /* Trial 1: a second response to START (same txn) arrived during the
             * STOP wait. A repeat of an already-answered request is logged and
             * ignored (bounded); anything else is still a protocol violation. */
            int seen = 0;
            for (unsigned k = 0; k < R->ndone && k < 16; k++) if (R->done_txn[k] == m.txn && R->done_id[k] == m.id) seen = 1;
            if (seen && ++R->dup_responses <= G1_MAX_DUP_RESPONSES) {
                emit(R, "A9_GNSS_G1 duplicate-response txn=%u id=0x%04x ignored\n", m.txn, m.id);
                continue;
            }
        }
        if (m.type != G1_QMI_RESPONSE || m.txn != txn || m.id != id) {
            emit(R, "A9_GNSS_G1 reject unexpected-response type=%u txn=%u id=0x%04x\n", m.type, m.txn, m.id); return -1;
        }
        R->done_txn[R->ndone%16] = txn; R->done_id[R->ndone%16] = id; R->ndone++;
        uint16_t err = 0; int res = g1_response_result(&m, &err);
        if (res < 0) { emit(R, "A9_GNSS_G1 reject response-malformed %s\n", what); return -1; }
        emit(R, "A9_GNSS_G1 response %s result=%s error=%u\n", what, res ? "failure" : "success", err);
        if(id==G1_MSG_INJECT_UTC_TIME && event(R,"A9_TIME_ERROR %u\n",err)) return -1;
        return res;
    }
}

static int request(struct run *R, const char *what, size_t (*enc)(uint8_t *, size_t, uint16_t), uint16_t id, int64_t limit)
{
    uint8_t b[128]; uint16_t txn=++R->txn;
    size_t n=enc(b,sizeof(b),txn);
    return request_frame(R,what,b,n,txn,id,limit);
}

static int inject_time(struct run *R, int64_t limit)
{
    if (!R->L->time_sample || R->time_sent >= 128 || now(R)-R->last_time < 10000) return 0;
    struct g1_time sample;
    int available=R->L->time_sample(R->L->ctx,&sample);
    if (available < 0) { emit(R,"A9_GNSS_G1 fault time-ipc\n"); return -1; }
    if (!available) return 0;
    R->last_time=now(R); ++R->time_sent;
    uint8_t b[32]; uint16_t txn=++R->txn;
    size_t n=g1_encode_time(b,sizeof(b),txn,sample.utc_ms,sample.uncertainty_ms);
    int r=request_frame(R,"inject-time",b,n,txn,G1_MSG_INJECT_UTC_TIME,limit);
    if (r >= 0 && event(R,"A9_TIME_ACK %u\n",(unsigned)r)) return -1;
    /* Modem rejection is observable, but does not abort an otherwise useful
     * standalone session. Transport/malformed responses retain STOP-on-error. */
    return r < 0 ? r : 0;
}

static void summary(struct run *R, const char *stop, int rc)
{
    minute_flush(R);
    emit(R, "A9_GNSS_G1 summary level=%s engine_on=%d sv_reports=%u sv_max=%u tracked_max=%u cn0_max=%.1f "
            "first_sv_ms=%lld first_track_ms=%lld fix_ms=%lld fix_hunc_m=%.1f fix_sv_used=%u pos_reports=%u pos_success=%u "
            "pos_fail=%u last_fail_status=%u nmea=%u gsv=%u unknown_tlvs=%u packets=%u dup_responses=%u master=%d/%d lock=%d/%u ttff_ms=%lld fix_records=%u fix_write_errors=%u stop=%s\n",
         g1_level_name(R->level), R->engine_on, R->sv_reports, R->sv_max, R->sv_tracked_max, (double)R->cn0_max,
         (long long)R->first_sv_ms, (long long)R->first_track_ms, (long long)R->fix_ms, (double)R->fix_hunc, R->fix_used,
         R->pos_reports, R->pos_success, R->fail_reports, R->last_fail_status, R->nmea_inds, R->nmea_gsv, R->unknown_tlvs,
         R->packets, R->dup_responses, R->master_ind, R->master_status, R->lock_ind, R->lock.type,
         (long long)(R->level == G1_FIX ? R->fix_ms - R->start_ack_ms : -1), R->fix_records, R->fix_write_errors, stop);
    emit(R, "A9_GNSS_G1 result=%s rc=%d\n", g1_level_name(R->level), rc);
}

int g1_run(const struct g1_link *L, const struct g1_opts *o, FILE *out)
{
    static struct run R;
    memset(&R, 0, sizeof(R));
    R.L = L; R.out = out; R.fix_fd = o ? o->fix_fd : -1; R.minute = -1; R.sv_detail=o ? o->sv_detail : 0;
    if (!o || o->seconds < G1_MIN_SECONDS || o->seconds > G1_MAX_SECONDS || o->post_fix_seconds < -1 ||
        o->post_fix_seconds > G1_POST_FIX_MAX || o->fix_fd < -1) { emit(&R, "A9_GNSS_G1 fault arguments\n"); summary(&R, "not-needed", 1); return 1; }
    R.t0 = now(&R);
    if (R.t0 < 0) { emit(&R, "A9_GNSS_G1 fault clock\n"); summary(&R, "not-needed", 1); return 1; }
    R.sec_start = R.t0; R.last_time=R.t0-10000;
    R.end = R.t0 + (int64_t)o->seconds * 1000;
    int64_t collect_end = R.end - G1_STOP_RESERVE_MS;
    emit(&R, "A9_GNSS_G1 begin v4 seconds=%d post_fix_s=%d fix_file=%d master=%d set_mode=%d mask=0x%llx client=%s type=%u recurrence=periodic interval_ms=%u accuracy=high share=%u\n",
         o->seconds, o->post_fix_seconds, o->fix_fd >= 0, o->master, o->set_mode, (unsigned long long)G1_EVENT_MASK, G1_CLIENT_STR, G1_CLIENT_TYPE, G1_MIN_INTERVAL_MS,
         G1_SHARE_POSITION);
    int rc = 1, started = 0, r;
    struct g1_msg m;

    if (o->master) {
        /* Stock open() registers the master client before any event mask. Stock
         * tolerates a refusal (mMasterRegisterNotSupported); so do we, logged. */
        r = request(&R, "register-master", g1_encode_register_master, G1_MSG_REGISTER_MASTER, collect_end);
        if (r < 0) goto out;
        if (r == 0) {
            int64_t until = now(&R) + G1_RESPONSE_MS;
            while (!R.master_ind) {
                int k = receive(&R, until, &m);
                if (k < 0) goto out;
                if (k == 0) { if (now(&R) >= until) { emit(&R, "A9_GNSS_G1 note master-indication-timeout\n"); break; } continue; }
                if (m.type != G1_QMI_INDICATION) { emit(&R, "A9_GNSS_G1 reject while-waiting-master\n"); goto out; }
                if (m.id==G1_MSG_REGISTER_MASTER || m.id==G1_MSG_GET_ENGINE_LOCK || m.id==G1_MSG_SET_OPERATION_MODE)
                    emit_hex(&R, "ind", R.rx, (size_t)m.len + G1_QMI_HEADER);
                if (on_indication(&R, &m)) goto out;
            }
        } else emit(&R, "A9_GNSS_G1 note master-registration-refused (continuing, as stock)\n");
    }
    r = request(&R, "reg-events", g1_encode_reg_events, G1_MSG_REG_EVENTS, collect_end);
    if (r) goto out;
    /* Read-only diagnostic: the engine lock state (no action is taken on it). */
    r = request(&R, "get-engine-lock", g1_encode_get_engine_lock, G1_MSG_GET_ENGINE_LOCK, collect_end);
    if (r < 0) goto out;
    if (r == 0) {
        int64_t until = now(&R) + G1_RESPONSE_MS;
        while (!R.lock_ind) {
            int k = receive(&R, until, &m);
            if (k < 0) goto out;
            if (k == 0) { if (now(&R) >= until) { emit(&R, "A9_GNSS_G1 note engine-lock-indication-timeout\n"); break; } continue; }
            if (m.type != G1_QMI_INDICATION) { emit(&R, "A9_GNSS_G1 reject while-waiting-lock\n"); goto out; }
            if (m.id==G1_MSG_REGISTER_MASTER || m.id==G1_MSG_GET_ENGINE_LOCK || m.id==G1_MSG_SET_OPERATION_MODE)
                emit_hex(&R, "ind", R.rx, (size_t)m.len + G1_QMI_HEADER);
            if (on_indication(&R, &m)) goto out;
        }
    }
    if (o->set_mode) {
        r = request(&R, "set-operation-mode", g1_encode_set_mode, G1_MSG_SET_OPERATION_MODE, collect_end);
        if (r) goto out;
        /* Stock waits for the 0x4a indication (locSyncSendReq, 1000 ms); require it. */
        int64_t until = now(&R) + G1_RESPONSE_MS;
        while (!R.set_mode_ind) {
            int k = receive(&R, until, &m);
            if (k < 0) goto out;
            if (k == 0) { if (now(&R) >= until) { emit(&R, "A9_GNSS_G1 fault mode-indication-timeout\n"); goto out; } continue; }
            if (m.type != G1_QMI_INDICATION || on_indication(&R, &m)) { emit(&R, "A9_GNSS_G1 reject while-waiting-mode\n"); goto out; }
        }
        if (R.set_mode_status != 0) { emit(&R, "A9_GNSS_G1 reject mode-indication status=%d\n", R.set_mode_status); goto out; }
        emit(&R, "A9_GNSS_G1 mode standalone confirmed\n");
    }
    if (inject_time(&R,collect_end)) goto out;
    started = 1;   /* from here a STOP is always attempted */
    r = request(&R, "start", g1_encode_start, G1_MSG_START, collect_end);
    if (r) goto stop;
    R.start_acked = 1;
    R.start_ack_ms = now(&R) - R.t0;
    raise_level(&R, G1_SESSION);
    for (;;) {
        if (now(&R) >= collect_end) break;
        if (inject_time(&R,collect_end)) goto stop;
        if (o->post_fix_seconds >= 0 && R.level == G1_FIX && now(&R) - R.t0 >= R.fix_ms + (int64_t)o->post_fix_seconds * 1000) {
            emit(&R, "A9_GNSS_G1 note post-fix window done (%d s after first fix)\n", o->post_fix_seconds);
            break;
        }
        int k = receive(&R, collect_end, &m);
        if (k == 0) { if (now(&R) >= collect_end) break; continue; }
        if (k < 0) goto stop;
        if (m.type != G1_QMI_INDICATION) {
            int seen = 0;
            emit_hex(&R, "resp", R.rx, (size_t)m.len + G1_QMI_HEADER);
            for (unsigned k = 0; k < R.ndone; k++) if (R.done_txn[k] == m.txn && R.done_id[k] == m.id) seen = 1;
            if (m.type == G1_QMI_RESPONSE && seen && ++R.dup_responses <= G1_MAX_DUP_RESPONSES) {
                emit(&R, "A9_GNSS_G1 duplicate-response txn=%u id=0x%04x ignored\n", m.txn, m.id); continue;
            }
            emit(&R, "A9_GNSS_G1 reject unsolicited-response id=0x%04x\n", m.id); goto stop;
        }
        if (on_indication(&R, &m)) goto stop;
    }
    rc = 0;
stop:
    if (started) {
        int64_t lim = now(&R) + G1_STOP_RESERVE_MS - 500;
        if (lim > R.end) lim = R.end;
        int s = request(&R, "stop", g1_encode_stop, G1_MSG_STOP, lim);
        if (s) rc = 1;
        summary(&R, s == 0 ? "acknowledged" : "failed", rc);
        return rc;
    }
out:
    summary(&R, "not-needed", 1);
    return 1;
}
