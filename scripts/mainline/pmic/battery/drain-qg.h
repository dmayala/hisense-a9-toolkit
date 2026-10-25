/* SPDX-License-Identifier: GPL-2.0-only */
/* Pure decoding/accounting for PMI632 QG batches, shared with the host test.
 * Stock: FIFO_I 0x48a0, signed-24 accumulator 0x488b, count 0x488e;
 * S2 sample count/interval 0x4851/52. No current is invented for wall time.
 */
#ifndef A9_DRAIN_QG_H
#define A9_DRAIN_QG_H

struct a9_qg_config {
	unsigned int length, samples, interval_ms, full_ms, wake_ms;
};

static inline int a9_qg_config(unsigned int s2, unsigned int interval,
			       unsigned int sleep, struct a9_qg_config *c)
{
	unsigned int limit;

	if (!interval || interval > 255)
		return -EINVAL;
	c->length = ((s2 >> 3) & 7) + 1;
	c->samples = 1U << ((s2 & 7) + 1);
	c->interval_ms = interval * 10;
	c->full_ms = c->length * c->samples * c->interval_ms;
	limit = c->length < ((sleep & 7) + 1) ? c->length : ((sleep & 7) + 1);
	c->wake_ms = limit * c->samples * c->interval_ms / 2;
	if (c->wake_ms > 30000)
		c->wake_ms = 30000;
	/* Do not turn a drain measurement into a sub-second wakeup benchmark. */
	return c->wake_ms >= 1000 ? 0 : -EOPNOTSUPP;
}

static inline int a9_qg_batch(const struct a9_qg_config *c, unsigned int length,
			      const short *fifo, unsigned int count, int acc,
			      long long elapsed_ms, long long *raw_ms,
			      unsigned int *covered_ms)
{
	long long sum = 0;
	unsigned int i;

	if (length >= c->length || elapsed_ms >= c->full_ms)
		return -EOVERFLOW;
	if (count >= c->samples || elapsed_ms < 0)
		return -EINVAL;
	for (i = 0; i < length; i++) {
		if (fifo[i] == -32768)
			return -ENODATA;
		sum += (long long)fifo[i] * c->samples;
	}
	if (count) {
		if (acc > (int)(32767 * count) || acc < -(int)(32767 * count))
			return -ERANGE;
		sum += acc;
	}
	*covered_ms = (length * c->samples + count) * c->interval_ms;
	/* Detect replay, overflow, missed wakeups and S3 gaps. Quantization and
	 * the short master-hold transaction are explicitly bounded, not filled.
	 */
	if (*covered_ms > elapsed_ms + 2 * c->interval_ms + 20)
		return -ERANGE;
	if ((long long)*covered_ms + 2 * c->interval_ms + 20 < elapsed_ms)
		return -ENODATA;
	*raw_ms = sum * c->interval_ms;
	return 0;
}
#endif
