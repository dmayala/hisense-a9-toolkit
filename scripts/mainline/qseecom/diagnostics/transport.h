/* SPDX-License-Identifier: Apache-2.0 */
#ifndef A9_RPMB_DIAG_TRANSPORT_H
#define A9_RPMB_DIAG_TRANSPORT_H
struct diag_transport { int fd; const char *path; };
int diag_send(const char *text, void *ctx);
#endif
