/* SPDX-License-Identifier: GPL-2.0-only */
/* Real QRTR transport for s2_link: one namespace lookup for SNS 400 v1 (exactly one instance), then one
 * datagram socket per connection that accepts packets only from that service address. Thread-safe. */
#ifndef S2_QRTR_H
#define S2_QRTR_H
#include "s2-client.h"
#include <pthread.h>
#ifdef __cplusplus
extern "C" {
#endif
#define S2_QRTR_MAX_CONN 16
struct s2_qrtr { uint32_t node, port; int fds[S2_QRTR_MAX_CONN]; pthread_mutex_t lock; };
/* Polls the QRTR namespace (one lookup per 500 ms) until exactly one SNS 400 v1 is advertised or wait_ms ends.
 * 0 found, -1 none/ambiguous/error. Fills L with the real ops. */
int s2_qrtr_init(struct s2_qrtr *q, struct s2_link *L, int wait_ms);
void s2_qrtr_destroy(struct s2_qrtr *q);
int64_t s2_mono_ms(void *ctx);
#ifdef __cplusplus
}
#endif
#endif
