/* SPDX-License-Identifier: GPL-2.0-only */
/* SNS client protocol steps shared by s2-sns-probe and the sensors HAL: one QRTR
 * connection per stream (as stock libssc), request/response with the first early
 * indication kept, indication -> event message decoding, SUID lookup, attributes,
 * stream start/stop. Thread-safe per connection: every connection owns its buffers.
 */
#ifndef S2_CLIENT_H
#define S2_CLIENT_H
#include "s2-sns.h"
#include <stdio.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Transport, so the offline tests can script the SSC side byte for byte. */
struct s2_link {
    int (*open)(void *ctx);                                                /* handle >= 0 */
    int (*send)(void *ctx, int h, const uint8_t *b, size_t n);             /* 0 ok */
    int (*recv)(void *ctx, int h, uint8_t *b, size_t cap, int timeout_ms); /* >0 len, 0 timeout, <0 error */
    void (*close)(void *ctx, int h);
    int64_t (*now_ms)(void *ctx);
    void *ctx;
};

#define S2_RESP_MS 2000
/* Review 79j N2: an early indication may be a jumbo (0x22, payload up to 62000), so the early buffer and the
 * receive buffer hold a full jumbo frame (7 + 11 + 3 + 2 + 62000 = 62023 bytes). */
#define S2_FRAME_MAX (S2_JUMBO_MAX + 64)

struct s2_conn {
    int h; uint16_t txn; uint64_t client_id; int has_client;
    size_t early_len; uint8_t early[S2_FRAME_MAX];
    uint8_t rx[S2_FRAME_MAX];
    struct s2_event_msg m;
};
/* NULL on failure (no memory or no transport handle) */
struct s2_conn *s2_conn_open(const struct s2_link *L);
void s2_conn_close(const struct s2_link *L, struct s2_conn *c);
/* 0 ok; -1 with a "S2-PROBE-FAIL <reason> <what>" line on log */
int s2_request(const struct s2_link *L, struct s2_conn *c, const uint8_t *pb, size_t pbn, FILE *log, const char *what);
/* next decoded client event message on this connection: 0 ok (*m set), 1 timeout, -1 error */
int s2_next_events(const struct s2_link *L, struct s2_conn *c, int64_t end_ms, struct s2_event_msg **m);
/* 1 found (*out = first SUID), 0 none within timeout, -1 error */
int s2_lookup(const struct s2_link *L, const char *data_type, struct s2_suid *out, int timeout_ms, FILE *log);
int s2_attributes(const struct s2_link *L, const char *data_type, struct s2_suid s, struct s2_attrs *a, int timeout_ms, FILE *log);
/* config request (514 on-change, else 513 at hz; NO_WAKEUP, batch_period 0) on a new connection */
struct s2_conn *s2_stream_start(const struct s2_link *L, const char *data_type, struct s2_suid s, int on_change, float hz, FILE *log);
/* disable (msg 10) then close; returns 0 if the disable was acknowledged */
int s2_stream_stop(const struct s2_link *L, struct s2_conn *c, struct s2_suid s, FILE *log);

#ifdef __cplusplus
}
#endif
#endif
