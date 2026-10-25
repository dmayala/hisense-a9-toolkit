/* Original conversion, retained as the byte/geometry reference. */
void reference(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
		    int c1, int r1, int c2, int r2, struct a9e_rect *changed)
{
	int c, r, bpp = fmt == A9E_FMT_R8 ? 1 : 4;
	int i0 = fmt == A9E_FMT_BGRX ? 2 : 0, i2 = fmt == A9E_FMT_BGRX ? 0 : 2;
	struct a9e_rect ch = { A9T_W, A9T_H, 0, 0 };

	if (c1 < 0) c1 = 0;
	if (r1 < 0) r1 = 0;
	if (c2 > A9E_FB_W) c2 = A9E_FB_W;
	if (r2 > A9E_FB_H) r2 = A9E_FB_H;
	for (c = c1; c < c2; c++) {
		int y = A9E_FB_W - 1 - c, rmin = A9T_W, rmax = -1;
		uint8_t *o = grey + (size_t)y * A9T_W;
		const uint8_t *p = src + (size_t)r1 * pitch + (size_t)c * bpp;
		for (r = r1; r < r2; r++, p += pitch) {
			uint8_t v = bpp == 1 ? p[0] :
				(uint8_t)((76u * p[i0] + 150u * p[1] + 30u * p[i2]) >> 8);
			if (changed && o[r] != v) {
				if (r < rmin) rmin = r;
				rmax = r;
			}
			o[r] = v;
		}
		if (rmax >= 0) {
			if (rmin < ch.x1) ch.x1 = rmin;
			if (rmax + 1 > ch.x2) ch.x2 = rmax + 1;
			if (y < ch.y1) ch.y1 = y;
			if (y + 1 > ch.y2) ch.y2 = y + 1;
		}
	}
	if (changed)
		a9e_rect_union(changed, &ch);
}
