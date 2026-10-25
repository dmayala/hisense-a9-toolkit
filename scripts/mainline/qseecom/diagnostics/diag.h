/* SPDX-License-Identifier: Apache-2.0 */
#ifndef A9_RPMB_DIAG_H
#define A9_RPMB_DIAG_H
#include <stddef.h>
#include <stdint.h>
#define DIAG_TEXT 256
#define DIAG_QUEUE 64
struct diag_state {
    char records[DIAG_QUEUE][DIAG_TEXT];
    unsigned head, count;
    uint32_t overrun, truncated, sent, dropped, readerr;
};
typedef int (*diag_submit)(const char *, void *);
int diag_parse(const char *record, size_t bytes, char output[DIAG_TEXT]);
void diag_record(struct diag_state *s, const char *record, size_t bytes);
void diag_flush(struct diag_state *s, diag_submit submit, void *ctx);
void diag_increment(uint32_t *n);
#endif
