/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_QSEECOM_BOUNDS_H
#define A9_QSEECOM_BOUNDS_H
#include <linux/types.h>

/* Stock TZ validates the sum, even though libQSEEComAPI's img_len includes MDT. */
static inline bool a9q_load_extent(__u32 mdt, __u32 image, __u32 limit,
				 __u32 *extent)
{
	if (!mdt || !image || mdt > image || image > limit || mdt > limit - image)
		return false;
	*extent = mdt + image;
	return true;
}

static inline bool a9q_range(__u64 base, __u32 size, __u64 ptr, __u32 len,
			     __u32 *off)
{
	if (!len || ptr < base || base > ~(__u64)0 - size ||
	    ptr - base > size || len > size - (ptr - base))
		return false;
	*off = ptr - base;
	return true;
}

static inline bool a9q_fd_offset(__u32 size, __u32 base, __u32 len, __u32 off,
				 bool wide, __u32 *absolute)
{
	if (base > size || len > size - base || off > len ||
	    (wide ? 8 : 4) > len - off || off >= (1U << 30))
		return false;
	*absolute = base + off;
	return true;
}
#endif
