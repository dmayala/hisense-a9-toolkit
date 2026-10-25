/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_RPMB_OBSERVE_H
#define A9_RPMB_OBSERVE_H
/* Public command/control metadata only. Never inspect/log data, MAC, nonce,
 * key IDs, counters or PTBL opaque contexts. This grants no write permission.
 */
struct a9_rpmb_observation {
	unsigned int shape, reason, request, frames, input_frames, offset, first, count;
	unsigned int table_candidate, table_valid;
};

static inline unsigned int a9_rpmb_public_context(bool keymaster, unsigned int cmd)
{
	if (!keymaster)
		return 0;
	switch (cmd) {
	case 0x108: case 0x508: case 0x2108: case 0x2508: return 1;
	case 0x10b: case 0x50b: case 0x210b: case 0x250b: return 2;
	case 0x2114: case 0x2514: return 3;
	case 0x2117: case 0x2517: return 4;
	case 0x10d: case 0x50d: case 0x210d: case 0x250d: return 5;
	case 0x10e: case 0x50e: case 0x210e: case 0x250e: return 6;
	case 0x1001: case 0x1501: case 0x21001: return 7;
	case 0x1002: case 0x1502: case 0x21002: return 8;
	case 0x1003: case 0x1503: case 0x21003: return 9;
	case 0x1004: case 0x1504: case 0x21004: return 10;
	default: return 0;
	}
}

static inline struct a9_rpmb_observation
a9_rpmb_observe_request(const unsigned char *buf, unsigned int size)
{
	struct a9_rpmb_observation r = { 0 };
	unsigned int offset, input, frames, type, command;
	const unsigned char *f;

	r.reason = 1;
	if (!buf || size < 20)
		return r;
	command = a9_rpmb_le32(buf);
	r.reason = 2;
	if (command != 0x102 && command != 0x103)
		return r;
	/* Project the actual map observer even when this diagnostic's stricter
	 * input-length checks fail. Never make gated flags look like its result.
	 */
	if (command == 0x102) {
		struct a9_rpmb_table_read t = a9_rpmb_table_request(buf, size);

		r.table_candidate = t.candidate;
		r.table_valid = t.valid;
	}
	frames = a9_rpmb_le32(buf + 4);
	input = a9_rpmb_le32(buf + 8);
	offset = a9_rpmb_le32(buf + 12);
	r.reason = 3;
	if (!frames || frames > A9_RPMB_GK_MAX_FRAMES)
		return r;
	r.reason = 4;
	if (!input || input % 512 || input / 512 > A9_RPMB_GK_MAX_FRAMES)
		return r;
	r.reason = 5;
	if (offset < 20 || offset > size || input > size - offset)
		return r;
	f = buf + offset;
	type = a9_rpmb_be16(f + 510);
	r.reason = 6;
	if ((command == 0x102 && type != 2 && type != 4) ||
	    (command == 0x103 && type != 3))
		return r;
	r.shape = 1;
	r.reason = 0;
	r.request = type;
	r.frames = frames;
	r.input_frames = input / 512;
	r.offset = offset;
	/* GET_WRITE_COUNTER has no addressed range. Do not print unused bytes. */
	if (type == 3 || type == 4) {
		r.first = a9_rpmb_be16(f + 504);
		r.count = a9_rpmb_be16(f + 506);
	}
	return r;
}

/* 0 unknown map/shape, 1 GK, 2 KM RR entries, 3 other KM bytes,
 * 4 outside both known extents, 5 crosses a known boundary. No guessed base.
 * offset is relative256-byte blocks for known GK/KM only; first is physical.
 */
static inline unsigned int a9_rpmb_observe_partition(const struct a9_rpmb_gk_map *m,
		unsigned int first, unsigned int n, unsigned int *offset)
{
	unsigned int end;

	*offset = 0;
	if (!m->end || !m->km_end || !n || first >= 65536 || n > 65536 - first)
		return 0;
	end = first + n;
	if (first >= m->first && end <= m->end) {
		*offset = first - m->first;
		return 1;
	}
	if (first >= m->km_first && end <= m->km_end) {
		*offset = first - m->km_first;
		if (a9_kmtb_map_ok(m) && first >= m->km_first + 32 && end <= m->km_first + 80)
			return 2;
		return 3;
	}
	if ((first < m->end && end > m->first) ||
	    (first < m->km_end && end > m->km_first))
		return 5;
	return 4;
}
#endif
