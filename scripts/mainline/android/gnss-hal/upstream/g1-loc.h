/* SPDX-License-Identifier: Apache-2.0
 * G1: one bounded standalone QMI LOC session (service 16, IDL major 2) over QRTR.
 *
 * Every id, TLV number and wire size below is taken from the stock
 * libloc_api_v02.so IDL tables (sha256 71dc1d1a..., service object 0x6cac8)
 * and the stock session code (startFix 0x157cbc, stopFix 0x1576c0,
 * locClientRegisterEventMask 0x160e4c); see runs/gnss-g1-prep.md.
 *
 * The client may send exactly seven request ids (the allowlist below). Every
 * other id, including NV/EFS, position/XTRA injection and engine-unlock messages, is refused
 * before encoding. It never prints coordinates or raw position NMEA.
 */
#ifndef A9_G1_LOC_H
#define A9_G1_LOC_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define G1_SERVICE 16u
#define G1_VERSION 2u

#define G1_QMI_REQUEST 0x00u
#define G1_QMI_RESPONSE 0x02u
#define G1_QMI_INDICATION 0x04u
#define G1_QMI_HEADER 7u
#define G1_MAX_MSG_LEN 10015u            /* service object max_msg_len */
#define G1_FRAME_MAX (G1_QMI_HEADER + G1_MAX_MSG_LEN)
#define G1_RX_BUFFER 16384u              /* > G1_FRAME_MAX: larger datagrams are rejected, never truncated */

/* Requests (the complete send allowlist) */
#define G1_MSG_REGISTER_MASTER 0x00bau     /* runtime client registration (stock registerMasterClient 0x15889c) */
#define G1_MSG_GET_ENGINE_LOCK 0x003bu     /* read-only query (stock getGpsLock 0x15af04) */
#define G1_MSG_INJECT_UTC_TIME 0x0038u
#define G1_IND_INJECT_TIME_REQUEST 0x0028u
#define G1_MSG_REG_EVENTS 0x0021u
#define G1_MSG_START 0x0022u
#define G1_MSG_STOP 0x0023u
#define G1_MSG_SET_OPERATION_MODE 0x004au
/* Indications decoded */
#define G1_IND_POSITION 0x0024u
#define G1_IND_SV_INFO 0x0025u
#define G1_IND_NMEA 0x0026u
#define G1_IND_ENGINE_STATE 0x002bu
#define G1_IND_FIX_SESSION_STATE 0x002cu

/* QMI_LOC_REG_EVENTS mask: position 0x1, SV 0x2, NMEA 0x4 (convertMask 0x143d88),
 * engine state 0x80 (stock pairs it with NMEA), fix session state 0x100.
 * UTC time request 0x10 is included. No position request, NI, Wi-Fi, sensor,
 * geofence or batching bits. */
#define G1_EVENT_MASK UINT64_C(0x197)
#define G1_SESSION_ID 1u
#define G1_RECURRENCE_PERIODIC 1u
#define G1_ACCURACY_HIGH 3u
#define G1_MIN_INTERVAL_MS 1000u
#define G1_ALTITUDE_ASSUMPTION_DISABLED 2u   /* stock startFix always sends 2 */
#define G1_MODE_STANDALONE 4u               /* stock startFix default branch */
#define G1_MASTER_KEY 0xbaabcdefu           /* stock registerMasterClient constant */
#define G1_CLIENT_STR "MHAL"                /* stock locClientRegisterEventMask, master path (string @0xff9e) */
#define G1_CLIENT_TYPE 1u                   /* stock: TLV 0x11 = 1 */
#define G1_SHARE_POSITION 1u                /* stock startFix TLV 0x18 (LocPosMode share_position) */
#define G1_MAX_DUP_RESPONSES 4u

#define G1_MIN_SECONDS 10
#define G1_MAX_SECONDS 2100                 /* G1b: 35 min */
#define G1_RESPONSE_MS 2000
#define G1_STOP_RESERVE_MS 5000
#define G1_MAX_PACKETS 40000u               /* whole session, any type (2100 s x ~4/s, with margin) */
#define G1_MAX_PACKETS_PER_S 64u
#define G1_MAX_OUTPUT 262144u               /* bytes of stdout */

/* Messages that can change persistent modem state or inject other data. All are
 * outside the allowlist; this list exists so tests and logs can name them. */
struct g1_forbidden { uint16_t id; const char *why; };
extern const struct g1_forbidden g1_forbidden_requests[];
extern const size_t g1_forbidden_count;
int g1_request_allowed(uint16_t msg_id);

/* ---- wire ---- */
struct g1_tlv { uint8_t type; uint16_t len; const uint8_t *v; };
#define G1_MAX_TLVS 96u
struct g1_msg {
    uint8_t type; uint16_t txn, id, len;
    unsigned ntlv; struct g1_tlv tlv[G1_MAX_TLVS];
};
/* 0 ok; -1 malformed (length mismatch, overrun, duplicate TLV, too many TLVs) */
int g1_parse(const uint8_t *b, size_t n, struct g1_msg *m);
const struct g1_tlv *g1_find(const struct g1_msg *m, uint8_t type);

/* Request encoders: return frame length, or 0 if refused/does not fit. */
size_t g1_encode_reg_events(uint8_t *b, size_t cap, uint16_t txn);
size_t g1_encode_set_mode(uint8_t *b, size_t cap, uint16_t txn);
size_t g1_encode_start(uint8_t *b, size_t cap, uint16_t txn);
size_t g1_encode_stop(uint8_t *b, size_t cap, uint16_t txn);
size_t g1_encode_register_master(uint8_t *b, size_t cap, uint16_t txn);
size_t g1_encode_get_engine_lock(uint8_t *b, size_t cap, uint16_t txn);
/* Generic gate used by all of the above (exposed for the refusal test). */
size_t g1_encode_request(uint8_t *b, size_t cap, uint16_t txn, uint16_t id, const uint8_t *tlvs, size_t tlv_len);

size_t g1_encode_time(uint8_t *b, size_t cap, uint16_t txn, uint64_t utc_ms, uint32_t uncertainty_ms);

/* Response TLV 0x02 (qmi_response_type): 0 = success, 1 = error, -1 malformed */
int g1_response_result(const struct g1_msg *m, uint16_t *error);

/* ---- indication decoders (strict against the stock IDL) ---- */
struct g1_fix {
    uint32_t status; uint8_t session; int has_latlon, has_hunc, has_tech, has_utc, has_used;
    double lat, lon; float hunc; uint32_t tech; uint64_t utc_ms; unsigned used; unsigned unknown_tlvs;
};
#define G1_MAX_SV 176u
struct g1_sv { uint32_t valid, system, status; uint16_t id; uint8_t health, info; float elev, azim, cn0; };
/* List selection mirrors stock LocApiV02::reportSv (0x13476c): the expanded list
 * (0x11) when present, else the legacy list (0x10). Both may be present. */
struct g1_sv_report {
    uint8_t alt_assumed; unsigned n; struct g1_sv sv[G1_MAX_SV]; unsigned unknown_tlvs; int expanded;
    int has_legacy, has_expanded, has_sig, has_rf; unsigned legacy_n, expanded_n, sig_n, rf_n;
};
struct g1_nmea_report { unsigned sentences; unsigned gsv, gsa, gga, rmc, other; unsigned unknown_tlvs; };
int g1_decode_position(const struct g1_msg *m, struct g1_fix *f);
int g1_decode_sv(const struct g1_msg *m, struct g1_sv_report *r);
int g1_decode_nmea(const struct g1_msg *m, struct g1_nmea_report *r);
int g1_decode_u32_status(const struct g1_msg *m, uint32_t *v);          /* engine state 0x2b, mode ind 0x4a */
int g1_decode_session_state(const struct g1_msg *m, uint32_t *state, int *has_id, uint8_t *id);
/* 0x3b indication: status, optional lockType (0x10), optional subType (0x11), optional lockClient (0x12) */
struct g1_lock { uint32_t status; int has_type, has_sub, has_client; uint32_t type, sub; uint64_t client; };
int g1_decode_engine_lock(const struct g1_msg *m, struct g1_lock *l);
/* Why the last decode failed (bounded diagnostics; no payload). */
enum g1_reason { G1R_NONE, G1R_NOT_INDICATION, G1R_WRONG_ID, G1R_UNKNOWN_MANDATORY, G1R_FIXED_SIZE, G1R_COUNT_MISSING,
                 G1R_COUNT_OVER_MAX, G1R_ARRAY_LENGTH, G1R_STRING_LENGTH, G1R_STRING_CHAR, G1R_MISSING_MANDATORY,
                 G1R_SEMANTIC };
const char *g1_reason_name(int r);
int g1_last_reject(uint8_t *tlv);   /* reason code; *tlv = offending TLV type (0 if none) */
/* Formats "type:len[/cN]" for up to 24 TLVs of m into buf (count byte only for
 * variable arrays known from the message's spec). Never copies values. */
size_t g1_tlv_summary(const struct g1_msg *m, char *buf, size_t cap);

/* ---- session ---- */
struct g1_time { uint64_t utc_ms; uint32_t uncertainty_ms; };
struct g1_link {
    /* 0 sent, -1 failed (one attempt; no retry) */
    int (*send)(void *ctx, const uint8_t *b, size_t n);
    /* >0 frame length, 0 timeout, -1 transport error, -2 datagram from another address (endpoint lost) */
    int (*recv)(void *ctx, uint8_t *b, size_t cap, int timeout_ms);
    int64_t (*now_ms)(void *ctx);   /* monotonic; <0 = clock failure */
    void *ctx;
    /* Optional, nonblocking: 1 fresh UTC sample, 0 none, -1 IPC failure.
     * Called serially between QMI requests. Successful samples are sent at
     * most once per ten seconds. */
    int (*time_sample)(void *ctx, struct g1_time *sample);
};
/* post_fix_seconds: -1 = collect the whole window; N >= 0 = STOP N s after the first fix.
 * fix_fd: -1 = none; else a pre-opened private file (root 0600, O_APPEND) that alone
 * receives fix records (coordinates). The console/transcript never carries them. */
struct g1_opts { int seconds; int set_mode; int post_fix_seconds; int master; int fix_fd; int sv_detail; };
#define G1_POST_FIX_MAX 300
#define G1_MAX_FIX_RECORDS 400u
#define G1_CN0_BUCKETS 8
/* Result levels, lowest to highest */
enum g1_level { G1_NONE, G1_SESSION, G1_ENGINE_ON, G1_SV_SEEN, G1_SV_TRACKED, G1_FIX };
const char *g1_level_name(int level);
/* 0: REG/START/STOP all acknowledged (any observation level); 1: failure.
 * Prints A9_GNSS_G1 lines to out (bounded). */
int g1_run(const struct g1_link *L, const struct g1_opts *o, FILE *out);
#endif
