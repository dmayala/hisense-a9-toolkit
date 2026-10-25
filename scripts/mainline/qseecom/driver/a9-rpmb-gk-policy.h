/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_RPMB_GK_POLICY_H
#define A9_RPMB_GK_POLICY_H
#define A9_RPMB_GK_MAX_FRAMES ((0x6400U - 20) / 512)
/* Stock TZ PTBL metadata only. This does not verify a response MAC. */
struct a9_rpmb_gk_map {
	unsigned int first, end, km_first, km_end;
};
struct a9_rpmb_table_read {
	unsigned int frames;
	bool candidate, valid;
};

static inline unsigned int a9_rpmb_le32(const unsigned char *p)
{
	return (unsigned int)p[0] | (unsigned int)p[1] << 8 |
	       (unsigned int)p[2] << 16 | (unsigned int)p[3] << 24;
}
static inline unsigned int a9_rpmb_be16(const unsigned char *p)
{
	return (unsigned int)p[0] << 8 | p[1];
}

/* Snapshot before SEND_RESP overwrites the request header and frame storage.
 * UFS kind9: validate ALL bounded type4 request descriptors, not just the
 * first address. Their counts must cover exactly physical table frames0..N-1.
 */
static inline struct a9_rpmb_table_read
a9_rpmb_table_request(const unsigned char *buf, unsigned int size)
{
	struct a9_rpmb_table_read r = { 0 };
	unsigned int offset, total, used = 0, index = 0, cap = 0;
	const unsigned char *f;

	if (!buf || size < 20 || a9_rpmb_le32(buf) != 0x102)
		return r;
	offset = a9_rpmb_le32(buf + 12);
	if (offset < 20 || offset > size || size - offset < 512)
		return r;
	f = buf + offset;
	if (a9_rpmb_be16(f + 510) != 4 || a9_rpmb_be16(f + 504))
		return r;
	r.candidate = true;
	total = a9_rpmb_le32(buf + 4);
	if (total != 1 && total != 8)
		return r;
	r.frames = total;
	while (used < total) {
		unsigned int n;

		if (index >= (size - offset) / 512)
			return r;
		f = buf + offset + index * 512;
		n = a9_rpmb_be16(f + 506);
		if (!index)
			cap = n;
		if (!n || n > total - used || n != (cap < total - used ? cap : total - used) ||
		    a9_rpmb_be16(f + 510) != 4 || a9_rpmb_be16(f + 508) ||
		    a9_rpmb_be16(f + 504) != used)
			return r;
		used += n;
		index++;
	}
	r.valid = true;
	return r;
}

/* Serialized PTBL bytes are concatenated 256-byte payloads, not raw512 frames. */
static inline unsigned int a9_rpmb_table_byte(const unsigned char *frames,
					   unsigned int index)
{
	return frames[(index / 256) * 512 + 228 + index % 256];
}
static inline unsigned int a9_rpmb_table_le(const unsigned char *frames,
					 unsigned int offset, unsigned int bytes)
{
	unsigned int value = 0, i;

	for (i = 0; i < bytes; i++)
		value |= a9_rpmb_table_byte(frames, offset + i) << (8 * i);
	return value;
}

static inline bool a9_rpmb_parse_gk_map(const unsigned char *frames,
			unsigned int nframes, struct a9_rpmb_gk_map *map)
{
	unsigned int format, count, reserve, limit, sum = 0, i, j;
	unsigned int gk = 0, km = 0, first = 0, end = 0, km_first = 0, km_end = 0;

	*map = (struct a9_rpmb_gk_map){ 0 };
	if (!frames || (nframes != 1 && nframes != 8))
		return false;
	for (i = 0; i < nframes; i++)
		if (a9_rpmb_be16(frames + i * 512 + 510) != 0x400 ||
		    a9_rpmb_be16(frames + i * 512 + 508))
			return false;
	if (a9_rpmb_table_le(frames, 0, 4) != 0x4c425450)
		return false;
	format = a9_rpmb_table_le(frames, 4, 4);
	if (format == 0x10100 && nframes == 1) {
		reserve = 1;
		limit = 15;
	} else if (format == 0x10200 && nframes == 8) {
		reserve = 8;
		limit = 127;
	} else {
		return false;
	}
	count = a9_rpmb_table_le(frames, 12, 4);
	if (!count || count > limit)
		return false;
	for (i = 0; i < count; i++) {
		unsigned int at = 16 + i * 16;
		unsigned int id = a9_rpmb_table_le(frames, at, 4);
		unsigned int start = a9_rpmb_table_le(frames, at + 4, 2);
		unsigned int length = a9_rpmb_table_le(frames, at + 6, 2);

		/* Physical RPMB addresses are16-bit and units are256 bytes. */
		if (!length || start < reserve || start >= 32768 || length > 32768 - start)
			return false;
		for (j = 0; j < i; j++) {
			unsigned int prev = 16 + j * 16;
			unsigned int pstart = a9_rpmb_table_le(frames, prev + 4, 2);
			unsigned int plen = a9_rpmb_table_le(frames, prev + 6, 2);

			/* Deletion leaves occupied tombstones with repeated IDffffffff. */
			if ((id != 0xffffffffU && id == a9_rpmb_table_le(frames, prev, 4)) ||
			    (start < pstart + plen && pstart < start + length))
				return false;
		}
		sum += length;
		if (id == 0x474b3232) {
			gk++;
			first = start * 2;
			end = (start + length) * 2;
		} else if (id == 0x4b4d5442) {
			km++;
			km_first = start * 2;
			km_end = (start + length) * 2;
		}
	}
	if (gk != 1 || km != 1 || sum != a9_rpmb_table_le(frames, 8, 4))
		return false;
	map->first = first;
	map->end = end;
	map->km_first = km_first;
	map->km_end = km_end;
	return true;
}

static inline bool a9_rpmb_table_reply(const unsigned char *buf, unsigned int size,
		struct a9_rpmb_table_read r, struct a9_rpmb_gk_map *map)
{
	*map = (struct a9_rpmb_gk_map){ 0 };
	if (!r.valid || !buf || size < 20 || a9_rpmb_le32(buf) != 0x102 ||
	    a9_rpmb_le32(buf + 4) ||
	    a9_rpmb_le32(buf + 12) != 20 ||
	    a9_rpmb_le32(buf + 8) != r.frames * 512 ||
	    r.frames * 512 > size - 20)
		return false;
	return a9_rpmb_parse_gk_map(buf + 20, r.frames, map);
}

/* A continuation may complete or request another listener. Fatal/unknown
 * secure statuses must never publish a staged map; this is not a MAC check.
 */
static inline bool a9_rpmb_map_scm_ok(int transport, unsigned long long result)
{
	return !transport && result <= 2;
}

/* Read only the12 public control bytes at frame+500, never a payload/MAC.
 * Each stock reliable-write group repeats address, count, counter and type;
 * successive groups cover the contiguous caller range. Unknown map denies.
 */
static inline bool a9_rpmb_gk_groups(const struct a9_rpmb_gk_map *map,
		unsigned int frames, bool (*get)(void *, unsigned int, unsigned char *),
		void *ctx)
{
	unsigned char head[12], next[12];
	unsigned int i = 0, previous_end = 0;

	if (!frames || frames > A9_RPMB_GK_MAX_FRAMES ||
	    !map->end || map->first >= map->end ||
	    frames > map->end - map->first)
		return false;
	while (i < frames) {
		unsigned int start, count, j;

		if (!get(ctx, i, head))
			return false;
		start = a9_rpmb_be16(head + 4);
		count = a9_rpmb_be16(head + 6);
		if (a9_rpmb_be16(head + 10) != 3 || a9_rpmb_be16(head + 8) ||
		    !count || count > frames - i || start < map->first ||
		    start >= map->end || count > map->end - start ||
		    (i && start != previous_end))
			return false;
		for (j = 1; j < count; j++) {
			unsigned int k;

			if (!get(ctx, i + j, next))
				return false;
			for (k = 0; k < sizeof(head); k++)
				if (head[k] != next[k])
					return false;
		}
		previous_end = start + count;
		i += count;
	}
	return true;
}
#endif
