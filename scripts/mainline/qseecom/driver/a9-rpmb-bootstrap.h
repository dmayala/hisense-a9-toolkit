/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_RPMB_BOOTSTRAP_H
#define A9_RPMB_BOOTSTRAP_H
/* Read-only requests through the existing stock listener. The caller owns
 * serialization, saves/restores the pending signed request, and erases scratch.
 * Nonce/type/result checks are not RPMB HMAC verification in normal world.
 */
struct a9_rpmb_bootstrap_ops {
	int (*exchange)(void *ctx);
	void (*nonce)(void *ctx, unsigned char *out, unsigned int bytes);
	void (*trace)(void *ctx, unsigned int phase, int rc);
};
static inline void a9_rpmb_put_le32(unsigned char *p, unsigned int value)
{
	unsigned int i;

	for (i = 0; i < 4; i++)
		p[i] = value >> (8 * i);
}
static inline void a9_rpmb_put_be16(unsigned char *p, unsigned int value)
{
	p[0] = value >> 8;
	p[1] = value;
}

/* Stock librpmb0x1074dc derives groups from its private cached capability,
 * not the descriptors' block_count. Use separate one-frame callbacks so it
 * cannot reinterpret a multi-frame request using a different group size.
 * Collect raw replies in private scratch; never submit them to secure world.
 * No counter query, DATA_WRITE, RESULT_READ or PROGRAM_KEY is synthesized.
 */
static inline int a9_rpmb_bootstrap_read(unsigned char *buf, unsigned int size,
		unsigned int first, unsigned int n, unsigned char *frames,
		const struct a9_rpmb_bootstrap_ops *ops, void *ctx)
{
	unsigned char nonce[16];
	unsigned int i;
	int ret;

	if (!n || n > A9_RPMB_GK_MAX_FRAMES || first >= 65536 || n > 65536 - first)
		return -EINVAL;
	if (size < 24 + 512)
		return -EMSGSIZE;
	for (i = 0; i < n; i++) {
		unsigned char *f = buf + 24;

		ops->nonce(ctx, nonce, sizeof(nonce));
		memset(buf, 0, size);
		a9_rpmb_put_le32(buf, 0x102);
		a9_rpmb_put_le32(buf + 4, 1);
		a9_rpmb_put_le32(buf + 8, 512);
		a9_rpmb_put_le32(buf + 12, 24);
		memcpy(f + 484, nonce, sizeof(nonce));
		a9_rpmb_put_be16(f + 504, first + i);
		a9_rpmb_put_be16(f + 506, 1);
		a9_rpmb_put_be16(f + 510, 4);
		ret = ops->exchange(ctx);
		if (ret)
			return ret;
		if (a9_rpmb_le32(buf) != 0x102 || a9_rpmb_le32(buf + 4) ||
		    a9_rpmb_le32(buf + 8) != 512 || a9_rpmb_le32(buf + 12) != 20)
			return -EIO;
		f = buf + 20;
		if (a9_rpmb_be16(f + 510) != 0x400 || a9_rpmb_be16(f + 508) ||
		    memcmp(f + 484, nonce, sizeof(nonce)))
			return -EBADMSG;
		memcpy(frames + i * 512, f, 512);
	}
	return 0;
}

static inline int a9_rpmb_bootstrap_map(bool *attempted,
		struct a9_rpmb_gk_map *map, unsigned char *buf, unsigned int size,
		unsigned char *frames, const struct a9_rpmb_bootstrap_ops *ops, void *ctx)
{
	unsigned int format;
	int ret;

	if (map->end)
		return 0;
	if (*attempted)
		return -ENODATA;
	*attempted = true;
	*map = (struct a9_rpmb_gk_map){ 0 };
	ret = a9_rpmb_bootstrap_read(buf, size, 0, 1, frames, ops, ctx);
	ops->trace(ctx, 2, ret);
	if (ret)
		return ret;
	if (a9_rpmb_table_le(frames, 0, 4) != 0x4c425450)
		return -EBADMSG;
	format = a9_rpmb_table_le(frames, 4, 4);
	if (format == 0x10200) {
		ret = a9_rpmb_bootstrap_read(buf, size, 0, 8, frames, ops, ctx);
		ops->trace(ctx, 3, ret);
		if (ret)
			return ret;
	} else if (format != 0x10100) {
		return -EBADMSG;
	}
	return a9_rpmb_parse_gk_map(frames, format == 0x10100 ? 1 : 8, map) ?
		0 : -EBADMSG;
}

/* A cached TA may already have read RR before the map was known. Obtain a
 * fresh before-image rather than guessing it was empty or relaxing the diff.
 * Unknown/delete-all contexts, bulk writes and non-RR bytes never qualify.
 */
static inline bool a9_rpmb_bootstrap_needs_rr(const struct a9_rpmb_gk_map *map,
		unsigned int context, bool valid, const unsigned char *saved, unsigned int size)
{
	struct a9_rpmb_observation o;

	if ((context != 1 && context != 2) || valid || !a9_kmtb_map_ok(map))
		return false;
	o = a9_rpmb_observe_request(saved, size);
	return o.shape && o.request == 3 && o.frames == 2 && o.input_frames == 2 &&
	       o.offset == 24 && o.first >= map->km_first + 32 &&
	       o.first <= map->km_first + 78 && !((o.first - map->km_first) % 2);
}
#endif
