/* SPDX-License-Identifier: GPL-2.0-only WITH Linux-syscall-note */
#ifndef A9_UFS_QUERY_ABI_H
#define A9_UFS_QUERY_ABI_H

#include <linux/types.h>

/* Qualcomm msm-4.19 include/uapi/scsi/ufs/ioctl.h, inline data, no pointer. */
#define UFS_IOCTL_QUERY 0x5388

struct ufs_ioctl_query_data {
	__u32 opcode;
	__u8 idn;
	__u16 buf_size;
	__u8 buffer[];
};

#endif
