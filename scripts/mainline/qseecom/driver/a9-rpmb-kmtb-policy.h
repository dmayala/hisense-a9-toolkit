/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_RPMB_KMTB_POLICY_H
#define A9_RPMB_KMTB_POLICY_H
/* Stock KMTB RR table: logical512-byte sectors16..39, 1536 opaque8-byte IDs.
 * Normal-world observations do not verify MACs; stock TA/listener are trusted.
 * Never log/copy these private images outside kernel RAM or change signed I/O.
 */
#define A9_KMTB_BYTES 0x3000U
#define A9_KMTB_FRAMES (A9_KMTB_BYTES / 256)
struct a9_kmtb_read { bool candidate, valid; };
struct a9_kmtb_state {
	bool valid, read_pending, read_this_call, pending, candidate;
	unsigned int context, first, operation, changed, reason, seen, counter;
	unsigned char table[A9_KMTB_BYTES], frames[1024];
};
static inline unsigned int a9_rpmb_be32(const unsigned char *p)
{
	return (unsigned int)p[0] << 24 | (unsigned int)p[1] << 16 |
	       (unsigned int)p[2] << 8 | p[3];
}
static inline bool a9_kmtb_map_ok(const struct a9_rpmb_gk_map *m)
{
	return m->km_end > m->km_first && m->km_end - m->km_first >= 80;
}
static inline bool a9_kmtb_in_partition(const struct a9_rpmb_gk_map *m,
				       unsigned int first, unsigned int n)
{
	return m->km_end && n && first < 65536 &&
	       first < m->km_end && n > (first < m->km_first ? m->km_first - first : 0);
}
/* Context is assigned from a known Keymaster app's public top-level command.
 * No alias mask that could accidentally include DELETE_ALL or unknown cmds.
 */
static inline unsigned int a9_kmtb_context(bool keymaster, unsigned int command)
{
	if (!keymaster)
		return 0;
	switch (command) {
	case 0x108: case 0x508: case 0x2108: case 0x2508: /* generate */
	case 0x10b: case 0x50b: case 0x210b: case 0x250b: /* import */
	case 0x2114: case 0x2514: /* modern upgrade */
	case 0x2117: case 0x2517: /* wrapped import */
		return 1;
	case 0x10d: case 0x50d: case 0x210d: case 0x250d:
		return 2;
	default: /* Includes ALL delete-all aliases. */
		return 0;
	}
}
static inline void a9_kmtb_begin(struct a9_kmtb_state *s, unsigned int context)
{
	if (s->pending)
		s->valid = false;
	s->pending = s->candidate = s->read_pending = s->read_this_call = false;
	s->context = context;
}
static inline void a9_kmtb_end(struct a9_kmtb_state *s, bool success)
{
	if (s->pending || (!success && s->read_this_call))
		s->valid = false;
	s->pending = s->candidate = s->read_pending = false;
	s->context = 0;
}
/* Snapshot every descriptor BEFORE userspace overwrites the request. */
static inline struct a9_kmtb_read a9_kmtb_read_request(const unsigned char *buf,
		unsigned int size, const struct a9_rpmb_gk_map *m)
{
	struct a9_kmtb_read r = { 0 };
	unsigned int offset, total, first, done = 0, index = 0, cap = 0;
	const unsigned char *f;

	if (!buf || size < 20 || a9_rpmb_le32(buf) != 0x102 || !a9_kmtb_map_ok(m))
		return r;
	offset = a9_rpmb_le32(buf + 12);
	if (offset < 20 || offset > size || size - offset < 512)
		return r;
	f = buf + offset;
	first = a9_rpmb_be16(f + 504);
	total = a9_rpmb_le32(buf + 4);
	r.candidate = a9_kmtb_in_partition(m, first, total);
	if (!r.candidate || first != m->km_first + 32 || total != A9_KMTB_FRAMES)
		return r;
	while (done < total) {
		unsigned int n;

		if (index >= (size - offset) / 512)
			return r;
		f = buf + offset + index++ * 512;
		n = a9_rpmb_be16(f + 506);
		if (!cap)
			cap = n;
		if (!n || n != (cap < total - done ? cap : total - done) ||
		    a9_rpmb_be16(f + 510) != 4 || a9_rpmb_be16(f + 508) ||
		    a9_rpmb_be16(f + 504) != first + done)
			return r;
		done += n;
	}
	r.valid = true;
	return r;
}
static inline void a9_kmtb_read_reply(struct a9_kmtb_state *s,
		const unsigned char *buf, unsigned int size, struct a9_kmtb_read r)
{
	unsigned int i;

	s->read_pending = false;
	if (!r.candidate)
		return;
	s->valid = false;
	if (!r.valid || !buf || size < 20 + A9_KMTB_FRAMES * 512 ||
	    a9_rpmb_le32(buf) != 0x102 || a9_rpmb_le32(buf + 4) ||
	    a9_rpmb_le32(buf + 8) != A9_KMTB_FRAMES * 512 ||
	    a9_rpmb_le32(buf + 12) != 20)
		return;
	for (i = 0; i < A9_KMTB_FRAMES; i++) {
		const unsigned char *f = buf + 20 + i * 512;

		if (a9_rpmb_be16(f + 510) != 0x400 || a9_rpmb_be16(f + 508))
			return;
	}
	for (i = 0; i < A9_KMTB_FRAMES; i++)
		memcpy(s->table + i * 256, buf + 20 + i * 512 + 228, 256);
	s->read_pending = true;
}
static inline bool a9_kmtb_controls(void *ctx, unsigned int index, unsigned char *out)
{
	const unsigned char *frames = ctx;

	if (index >= 2)
		return false;
	memcpy(out, frames + index * 512 + 500, 12);
	return true;
}
static inline bool a9_kmtb_zero(const unsigned char *p)
{
	unsigned int i;

	for (i = 0; i < 8; i++)
		if (p[i])
			return false;
	return true;
}
/* Exactly one ID transition, never replace a nonzero ID or clear neighbours.
 * Full-table uniqueness is stricter than stock's unchecked random-ID edge case.
 */
static inline unsigned int a9_kmtb_difference(const unsigned char *table,
		unsigned int at, const unsigned char *next, unsigned int *changed)
{
	unsigned int i, slot = 0, operation, matches = 0;
	const unsigned char *id;

	*changed = 0;
	for (i = 0; i < 512; i += 8)
		if (memcmp(table + at + i, next + i, 8)) {
			(*changed)++;
			slot = i;
		}
	if (*changed != 1)
		return 0;
	if (a9_kmtb_zero(table + at + slot) && !a9_kmtb_zero(next + slot)) {
		operation = 1;
		id = next + slot;
	} else if (!a9_kmtb_zero(table + at + slot) && a9_kmtb_zero(next + slot)) {
		operation = 2;
		id = table + at + slot;
	} else {
		return 0;
	}
	for (i = 0; i < A9_KMTB_BYTES; i += 8)
		if (!memcmp(table + i, id, 8))
			matches++;
	return matches == (operation == 1 ? 0U : 1U) ? operation : 0;
}
/* Stage the COMPLETE stock512-byte-sector update before listener wakeup.
 * Its two256-byte payloads may travel in one group or two stock groups.
 */
static inline void a9_kmtb_prepare(struct a9_kmtb_state *s,
		const struct a9_rpmb_gk_map *m, const unsigned char *buf, unsigned int size)
{
	unsigned int offset, total, first, at, i;
	unsigned char next[512];
	struct a9_rpmb_gk_map rr = { 0 };

	if (s->pending)
		s->valid = false; /* prior outcome uncertain */
	s->pending = s->candidate = false;
	s->changed = s->operation = s->seen = 0;
	s->reason = 4;
	if (!buf || size < 24 || a9_rpmb_le32(buf) != 0x103)
		return;
	offset = a9_rpmb_le32(buf + 12);
	if (offset != 24 || offset > size || size - offset < 512)
		return;
	first = a9_rpmb_be16(buf + offset + 504);
	total = a9_rpmb_le32(buf + 4);
	s->candidate = a9_kmtb_in_partition(m, first, total);
	if (!s->candidate)
		return;
	if (!a9_kmtb_map_ok(m)) { s->reason = 1; return; }
	if (!s->valid) { s->reason = 2; return; }
	if (!s->context) { s->reason = 3; return; }
	if (total != 2 || size - offset < 1024 ||
	    a9_rpmb_le32(buf + 8) != 1024 || a9_rpmb_le32(buf + 16) != 256 ||
	    a9_rpmb_le32(buf + 20) != a9_rpmb_be16(buf + offset + 506) ||
	    first < m->km_first + 32 ||
	    first >= m->km_first + 80 || (first - m->km_first) % 2)
		return;
	rr.first = m->km_first + 32;
	rr.end = m->km_first + 80;
	if (!a9_rpmb_gk_groups(&rr, 2, a9_kmtb_controls, (void *)(buf + offset)))
		return;
	/* For two reliable groups, stock increments the counter between groups. */
	if (a9_rpmb_be16(buf + offset + 506) == 1 &&
	    (a9_rpmb_be32(buf + offset + 500) == ~0U ||
	     a9_rpmb_be32(buf + offset + 1012) != a9_rpmb_be32(buf + offset + 500) + 1))
		return;
	s->counter = a9_rpmb_be32(buf + offset + 1012);
	if (s->counter == ~0U)
		return;
	at = (first - rr.first) * 256;
	for (i = 0; i < 2; i++)
		memcpy(next + i * 256, buf + offset + i * 512 + 228, 256);
	s->operation = a9_kmtb_difference(s->table, at, next, &s->changed);
	memset(next, 0, sizeof(next));
	if (!s->operation || s->operation != s->context) { s->reason = 5; return; }
	memcpy(s->frames, buf + offset, sizeof(s->frames));
	s->first = first;
	s->reason = 0;
	s->pending = true;
}
/* Match signed bytes exactly; accept only stock transport retransmission.
 * The SCSI shape/group checks remain mandatory at the caller.
 */
static inline bool a9_kmtb_allow(struct a9_kmtb_state *s,
		const unsigned char *frames, unsigned int bytes)
{
	unsigned int start, index;

	if (!s->valid || !s->pending || !s->context || !frames ||
	    (bytes != 512 && bytes != 1024) ||
	    a9_rpmb_be16(frames + 506) != bytes / 512)
		return false;
	start = a9_rpmb_be16(frames + 504);
	if (start < s->first || start > s->first + 1)
		return false;
	index = start - s->first;
	if (index * 512 + bytes > sizeof(s->frames) ||
	    memcmp(frames, s->frames + index * 512, bytes))
		return false;
	s->seen |= bytes == 1024 ? 3U : 1U << index;
	return true;
}
static inline bool a9_kmtb_write_reply(struct a9_kmtb_state *s,
		const unsigned char *buf, unsigned int size)
{
	const unsigned char *f;

	if (!s->pending || s->seen != 3 || !buf || size < 532 ||
	    a9_rpmb_le32(buf) != 0x103 || a9_rpmb_le32(buf + 4) ||
	    a9_rpmb_le32(buf + 8) != 512 || a9_rpmb_le32(buf + 12) != 20)
		return false;
	f = buf + 20;
	return a9_rpmb_be16(f + 510) == 0x300 && !a9_rpmb_be16(f + 508) &&
	       a9_rpmb_be32(f + 500) == s->counter + 1;
}
static inline void a9_kmtb_write_complete(struct a9_kmtb_state *s,
		const struct a9_rpmb_gk_map *m, bool success)
{
	unsigned int at, i;

	if (s->pending) {
		if (success && a9_kmtb_map_ok(m) &&
		    s->first >= m->km_first + 32 && s->first <= m->km_first + 78 &&
		    !((s->first - m->km_first) % 2)) {
			at = (s->first - m->km_first - 32) * 256;
			for (i = 0; i < 2; i++)
				memcpy(s->table + at + i * 256, s->frames + i * 512 + 228, 256);
		} else {
			s->valid = false;
		}
	}
	s->pending = false;
}
#endif
