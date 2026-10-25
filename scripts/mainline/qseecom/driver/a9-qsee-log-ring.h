/* SPDX-License-Identifier: GPL-2.0-only */
/* Stock tz_log.c's u16 wrap/offset ring; no secure bytes leave this helper. */
#ifndef A9_QSEE_LOG_RING_H
#define A9_QSEE_LOG_RING_H
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
typedef uint16_t u16;
typedef uint32_t u32;
#endif
struct a9_qsee_log_cursor { u16 wrap, offset; };
struct a9_qsee_log_span { u32 start, length; struct a9_qsee_log_cursor next; };

static inline int a9_qsee_log_span(struct a9_qsee_log_cursor start,
	struct a9_qsee_log_cursor end, u32 capacity, u32 count,
	struct a9_qsee_log_span *span)
{
	u16 wraps = end.wrap - start.wrap;
	u32 available;
	if (!capacity || capacity > 65535 || start.offset >= capacity ||
	    end.offset >= capacity)
		return -1;
	/* Leave one byte behind the producer when the consumer was overrun. */
	if (wraps > 1 || (wraps == 1 && end.offset > start.offset)) {
		start.wrap = end.wrap - 1;
		start.offset = (end.offset + 1) % capacity;
		if (!start.offset)
			start.wrap++;
	}
	if (start.wrap == end.wrap && start.offset > end.offset)
		return -1;
	available = start.wrap == end.wrap ? (u32)(end.offset - start.offset) :
		capacity - start.offset + end.offset;
	span->start = start.offset;
	span->length = available < count ? available : count;
	if (span->length > capacity - start.offset)
		span->length = capacity - start.offset;
	span->next = start;
	span->next.offset += span->length;
	if (span->next.offset == capacity) {
		span->next.offset = 0;
		span->next.wrap++;
	}
	return span->length ? 1 : 0;
}
#endif
