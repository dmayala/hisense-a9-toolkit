/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef S1_RPC_H
#define S1_RPC_H
#include <stddef.h>
#include <stdint.h>
#include "s1-registry-fs.h"
#define S1_RPC_CAP 65536u
#define S1_RPC_ARGS 32u
struct s1_rpc_arg { uint8_t *data; size_t len; };
struct s1_rpc_call {
    uint32_t scalar;
    unsigned in_count, out_count;
    struct s1_rpc_arg args[S1_RPC_ARGS];
    uint8_t reply[S1_RPC_CAP];
    size_t reply_len;
};
/* Qualcomm adsp_listener next2 packed arguments, independently reconstructed
 * from stock libadsprpc; evidence/provenance in s1-rpc-wire.md. Not a new frame.
 * unpack rejects all handles, unknown scalar bits, truncation and >64 KiB.
 */
int s1_rpc_unpack(struct s1_rpc_call *, uint32_t scalar, uint8_t *input, size_t size);
int s1_rpc_dispatch(struct s1_registry *, uint32_t module, struct s1_rpc_call *);
int s1_rpc_attach(int fd);
int s1_rpc_listener_init(int fd);
int s1_rpc_default_start(int fd);
/* Blocking service loop. Caller MUST enforce an independent process deadline
 * and stop ADSP on any failure. No device or filesystem path is opened here.
 * Every ioctl uses the upstream Linux fastrpc uAPI, never vendor ioctl aliases.
 */
int s1_rpc_listen(int fd, struct s1_registry *);
/* S2 service mode (long-lived, for normal boots): no 180 s / 4096-request
 * lifetime caps; instead at most S1_RPC_SERVICE_BURST requests in ANY
 * S1_RPC_SERVICE_WINDOW_MS interval (a true rolling window over the admission
 * times, review 79j N3; a runaway peer still fails closed with E2BIG).
 * S1 mode (service=0) is byte-for-byte the S1 behaviour. */
#define S1_RPC_SERVICE_BURST 4096u
#define S1_RPC_SERVICE_WINDOW_MS 60000u
struct s1_rpc_budget { uint64_t admitted_ms[S1_RPC_SERVICE_BURST]; unsigned oldest, count; };
/* 0 = request admitted; -1 = budget exhausted for the current window. */
int s1_rpc_budget_take(struct s1_rpc_budget *, uint64_t now_ms);
int s1_rpc_listen_mode(int fd, struct s1_registry *, int service);
#endif
