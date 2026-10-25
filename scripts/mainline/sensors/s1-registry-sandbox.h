/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef S1_REGISTRY_SANDBOX_H
#define S1_REGISTRY_SANDBOX_H
#include "s1-registry-fs.h"
/* Production only: requires root with chroot/setpcap before dropping all caps.
 * Installs a seccomp filter after chroot, no_new_privs, capability clearing.
 * The only ioctl permitted afterwards is upstream INVOKE/ATTACH_SNS on rpc_fd.
 */
int s1_registry_sandbox(struct s1_registry *registry, int rpc_fd);
#endif
