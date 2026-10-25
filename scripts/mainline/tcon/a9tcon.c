/* a9tcon core: see a9tcon.h. No vendor data, no allocation, memcpy/memset only.
 * Stock addresses refer to vendor.qti.hardware.display.composer-service,
 * sha256 8c89557b... (vaddr == file offset); docs/mainline-plan.md section 4. */
#include "a9tcon.h"

#include <string.h>

/* floor(v / 2^n), independent of how the compiler shifts negative values */
static inline int32_t asr(int32_t v, int n)
{
    return v >= 0 ? (v >> n) : -(int32_t)((uint32_t)(-(int64_t)v + ((1 << n) - 1)) >> n);
}

/* ---- tables ------------------------------------------------------------- */
struct cls_desc {
    size_t off;      /* offset of the class inside struct a9t_tables */
    uint32_t rs;     /* row stride */
    uint32_t slots;  /* rows loaded per frame */
};

static const struct cls_desc CLS[A9T_NCLASS] = {
    [A9T_CLEAR3] = {offsetof(struct a9t_tables, clear3), A9T_RS_CLEAR, 257},
    [A9T_TYPE2] = {offsetof(struct a9t_tables, type2), A9T_RS_CLEAR, 257},
    [A9T_BALANCED] = {offsetof(struct a9t_tables, balanced), A9T_RS_BAL, 256},
    [A9T_SMOOTH] = {offsetof(struct a9t_tables, smooth), A9T_RS_SMOOTH, 256},
    [A9T_SPEED] = {offsetof(struct a9t_tables, speed), A9T_RS_SMOOTH, 257},
};

static enum a9t_class type_class(uint32_t type)
{
    switch (type) {
    case 1: return A9T_BALANCED;
    case 2: return A9T_TYPE2;
    case 6: case 8: return A9T_SMOOTH;
    case 9: return A9T_SPEED;
    default: return A9T_CLEAR3; /* 3, 4, 5, 7 and anything else */
    }
}

static const uint8_t *cls_bin(const struct a9t_tables *t, enum a9t_class c, int bin)
{
    return (const uint8_t *)t + CLS[c].off + (size_t)bin * A9T_SLOTS * CLS[c].rs;
}

/* ---- setup -------------------------------------------------------------- */
void a9t_init(struct a9t_state *s, const struct a9t_tables *t, uint8_t *cache)
{
    memset(s, 0, sizeof(*s));
    s->t = t;
    s->cache = cache;
    memset(cache, 0, A9T_FRAME_BYTES);
    a9t_set_controls(s, 30, 30, 0);
    /* shadow stays 0: the stock-compatible Clear curve unless a caller opts in */
}

/* Clear UI shadow curve (mainline policy, NOT the stock calcG16Table): near-black
 * text levels go to 0 while light greys (>= 200) keep their exact bytes.
 * docs/mainline-eink-quality.md, runs/eink-stock-policy.md "Weak black in Clear". */
uint8_t a9t_clear_ui_grey(uint8_t x)
{
    if (x <= 30)
        return 0;
    if (x < 200)
        return (uint8_t)((x - 30) * 200 / 170);
    return x;
}

void a9t_set_clear_shadow(struct a9t_state *s, int on)
{
    s->clear_shadow = on ? 1 : 0;
}

/* grey lookup builders 0x1a48c8 (g2) / 0x1a49a4 (g16), EpdSetContrast knobs */
void a9t_set_controls(struct a9t_state *s, int32_t black, int32_t white, int32_t contrast)
{
    int32_t b = (black >= 0 && black <= 100) ? black : 30;
    int32_t h = (white >= 0 && white <= 100) ? 255 - (7 * white / 20) : 244;
    int32_t m = (contrast >= 0 && contrast <= 100) ? 50 + 80 * (100 - contrast) / 100 : 130;
    int32_t c = (contrast >= 0 && contrast <= 100) ? contrast : 0;
    for (int32_t x = 0; x < 256; x++) {
        int32_t q;
        if (b < x && x <= 200)
            q = (x - b) * m / (200 - b);
        else if (201 <= x && x < h)
            q = (x - 200) * (255 - m) / (h - 200) + m;
        else
            q = x < h ? 0 : 255;
        s->g2[x] = (uint8_t)q;
        if (c == 0)
            s->g16[x] = (uint8_t)x;
        else if (x <= 200)
            s->g16[x] = (uint8_t)(x * (200 - c) / 200);
        else
            s->g16[x] = (uint8_t)((x - 200) * (c + 55) / 55 + 200 - c);
    }
    /* shadow-then-G16, Clear (mode low byte 3) only */
    for (int32_t x = 0; x < 256; x++)
        s->g16s[x] = s->g16[a9t_clear_ui_grey((uint8_t)x)];
}

/* EinkSwTconThreadHandler 0x1a7a94..0x1a7b28 */
static int32_t clamp_t(int32_t t)
{
    if ((uint32_t)t & 0x80)
        return 0; /* stock quirk: bit 7 set reads as 0 */
    return t < 50 ? t : 50;
}

int a9t_temp_bin(const struct a9t_state *s, int32_t t1, int32_t t2)
{
    /* int64: stock traps (b.vs -> abort) where this would overflow int32 */
    int64_t sum = 2 * (int64_t)clamp_t(t1) + clamp_t(t2);
    int64_t avg = sum < 3 ? 0 : (sum > 152 ? 50 : sum / 3);
    return s->t->bin_map[avg];
}

/* ---- RGBA -> grey + rotation (0x1a61b0..0x1a62f8, same code at 0x1a5e0c) ---
 * luma (76 c0 + 150 c1 + 30 c2) >> 8 over the 832-stride portrait buffer, then
 * 8x8 transposes: landscape grey[y][x] = luma(src row x, column 823 - y). */
void a9t_rgba_to_grey(const uint8_t *rgba, uint8_t *grey)
{
    for (int y = 0; y < A9T_H; y++) {
        const uint8_t *p = rgba + (size_t)(A9T_H - 1 - y) * 4;
        uint8_t *o = grey + (size_t)y * A9T_W;
        for (int x = 0; x < A9T_W; x++, p += A9T_SRC_STRIDE * 4)
            o[x] = (uint8_t)((76u * p[0] + 150u * p[1] + 30u * p[2]) >> 8);
    }
}

/* ---- dithers (rolling rows: row k lives in w->row[k & 3]) ---------------- */
static void load_row(int32_t *dst, const uint8_t *grey, const uint8_t *lut, int y)
{
    const uint8_t *g = grey + (size_t)y * A9T_W;
    for (int x = 0; x < A9T_W; x++)
        dst[x] = lut[g[x]];
}

static void prime(struct a9t_work *w, const uint8_t *grey, const uint8_t *lut, int rows)
{
    for (int k = 0; k < rows && k < A9T_H; k++)
        load_row(w->row[k], grey, lut, k);
}

/* serpentine 1-bit error diffusion, 0x1a741c..0x1a7640 */
static void errdiff_1bit(const uint8_t *grey, const uint8_t *lut, uint8_t *lv, struct a9t_work *w)
{
    prime(w, grey, lut, 2);
    for (int y = 0; y < A9T_H; y++) {
        int32_t *cur = w->row[y & 1], *nxt = w->row[(y + 1) & 1];
        int rev = y & 1, last = y >= A9T_H - 1, f = rev ? -1 : 1;
        for (int scan = 0; scan < A9T_W; scan++) {
            int x = rev ? A9T_W - 1 - scan : scan;
            int32_t p = cur[x];
            int32_t e = p > 127 ? p - 255 : p;
            lv[(size_t)y * A9T_W + x] = p > 127 ? 15 : 0;
            if (scan < A9T_W - 1)
                cur[x + f] += asr(5 * e, 4);
            if (!last) {
                nxt[x] += asr(7 * e, 4);
                if (scan < A9T_W - 1 && !(scan & 1))
                    nxt[x + f] += asr(e, 3);
                else if (scan & 1)
                    nxt[x - f] += asr(e, 3);
            }
        }
        if (y + 2 < A9T_H)
            load_row(cur, grey, lut, y + 2);
    }
}

/* 32x32 ordered dither, 0x1a764c / 0x1a78c8 */
static void ordered_1bit(const struct a9t_state *s, const uint8_t *grey, const uint8_t *lut,
                         uint8_t *lv)
{
    const uint8_t *m = s->t->dither;
    for (int y = 0; y < A9T_H; y++)
        for (int x = 0; x < A9T_W; x++) {
            size_t i = (size_t)y * A9T_W + x;
            uint8_t a = lut[grey[i]];
            lv[i] = (a > m[(y & 31) * 32 + (x & 31)] || a > 0xef) ? 15 : 0;
        }
}

/* DitherSStyle16, 0x1a8300: 16-level serpentine diffusion over 3 rows below */
static void dither16(const uint8_t *grey, const uint8_t *lut, uint8_t *lv, struct a9t_work *w)
{
    prime(w, grey, lut, 4);
    for (int y = 0; y < A9T_H; y++) {
        int32_t *r0 = w->row[y & 3], *r1 = w->row[(y + 1) & 3];
        int32_t *r2 = w->row[(y + 2) & 3], *r3 = w->row[(y + 3) & 3];
        int rev = y & 1, low = y <= A9T_H - 4, f = rev ? -1 : 1;
        for (int scan = 0; scan < A9T_W; scan++) {
            int x = rev ? A9T_W - 1 - scan : scan;
            int32_t p = r0[x];
            int32_t q = p / 16, r = p - q * 16, e;
            uint8_t o;
            if (r <= 7) {
                o = (uint8_t)(q * 16);
                e = r;
            } else if (p > 0xef) {
                o = 0xff;
                e = p - 255;
            } else {
                o = (uint8_t)((q + 1) * 16);
                e = p - 16 * (q + 1);
            }
            int32_t e8 = asr(e, 3), e38 = asr(3 * e, 3);
            int32_t d = e8 / 3, d2 = e38 / 3, h = e8 / 2;
            int k = scan % 3;
            if (scan <= A9T_W - 2)
                r0[x + f] += k <= 1 ? e8 : h;
            if (low) {
                r1[x] += d;
                r3[x] += d;
                r2[x] += (rev && k == 2) ? h : d;
                if (scan <= A9T_W - 2 && !(scan & 1)) {
                    r1[x + f] += d2 + (k == 2 ? h : 0);
                    r2[x + f] += d2;
                    r3[x + f] += d2;
                } else if (scan & 1) {
                    r1[x - f] += d2;
                    r2[x - f] += d2;
                    r3[x - f] += d2;
                }
            }
            lv[(size_t)y * A9T_W + x] = o >> 4;
        }
        if (y + 4 < A9T_H)
            load_row(r0, grey, lut, y + 4);
    }
}

/* conditioning 0x1a7338..0x1a79b8 (jump table 0x2ac3c on the mode's low byte) */
void a9t_condition(const struct a9t_state *s, const uint8_t *grey, uint32_t mode,
                   uint8_t *lv, struct a9t_work *w)
{
    uint32_t lo = mode & 0xff, hi = (mode >> 8) & 0xff;
    switch (lo) {
    case 1:
    case 6:
        if (hi & 4)
            ordered_1bit(s, grey, s->g2, lv);
        else
            errdiff_1bit(grey, s->g2, lv, w);
        return;
    case 9:
        ordered_1bit(s, grey, s->g2, lv);
        return;
    case 2:
        dither16(grey, s->g16, lv, w);
        return;
    case 3: {
        const uint8_t *lut = s->clear_shadow ? s->g16s : s->g16;
        if (hi & 1) {
            dither16(grey, lut, lv, w);
            return;
        }
        for (size_t i = 0; i < A9T_N; i++)
            lv[i] = lut[grey[i]] >> 4;
        return;
    }
    default: /* 0, 4, 5, 7, 8: no conditioning */
        for (size_t i = 0; i < A9T_N; i++)
            lv[i] = grey[i] >> 4;
    }
}

/* packing 0x1a7a00 (plain) / 0x1a7e64 (mode 3, clear 0: the 256 sentinel) */
void a9t_pack(const uint8_t *old, const uint8_t *nw, uint32_t mode, int clear, uint16_t *pairs)
{
    for (size_t i = 0; i < A9T_N; i++)
        pairs[i] = (uint16_t)(old[i] << 4 | nw[i]);
    if ((mode & 0xff) != 3 || clear)
        return;
#define STEADY(j) (old[j] == 15 && nw[j] == 15)
    for (int y = 0; y < A9T_H; y++)
        for (int x = 0; x < A9T_W; x++) {
            size_t i = (size_t)y * A9T_W + x;
            if (old[i] == 15 || nw[i] != 15)
                continue;
            if (x > 0 && STEADY(i - 1))
                pairs[i - 1] = 256;
            else if (x < A9T_W - 1 && STEADY(i + 1))
                pairs[i + 1] = 256;
            else if (y > 0 && STEADY(i - A9T_W))
                pairs[i - A9T_W] = 256;
            else if (y < A9T_H - 1 && STEADY(i + A9T_W))
                pairs[i + A9T_W] = 256;
        }
#undef STEADY
}

/* 0x1a7a68..0x1a7a8c: clear on types 1/3 selects the type-2 table */
int a9t_final_type(uint32_t mode, int clear)
{
    uint32_t t = mode & 0xff;
    return ((t | 2) == 3 && clear) ? 2 : (int)t;
}

/* ---- frames (lookUpTable 0x1d1690) --------------------------------------- */
static void load_lut(struct a9t_state *s, enum a9t_class c, int bin, uint32_t frame)
{
    const uint8_t *p = cls_bin(s->t, c, bin) + frame + 1;
    for (uint32_t i = 0; i < CLS[c].slots; i++)
        s->scratch[i] = p[i * CLS[c].rs];
}

/* Byte 0 of every word := 0, bytes 1..3 untouched: the no-drive frame. On a
 * template-filled buffer this IS the template, stock's idle frame (stock never
 * writes bytes 1..3 after building the control template at TCON start). */
static void clear_codes(uint8_t *out)
{
    for (size_t i = 0; i < A9T_FRAME_BYTES; i += 4)
        out[i] = 0;
}

/* Writes byte 0 of every word; bytes 1..3 are never written (as stock). */
static void render(const struct a9t_state *s, const uint16_t *pairs, uint8_t *out)
{
    const uint8_t *lut = s->scratch; /* pairs must be <= 256 */
    for (int r = 0; r < A9T_TH; r++) {
        uint8_t *o = out + (size_t)r * A9T_TW * 4;
        int w = 0;
        if (r >= A9T_HEAD_ROWS) {
            const uint16_t *p = pairs + (size_t)(r - A9T_HEAD_ROWS) * A9T_W;
            for (; w < A9T_DATA_WORDS; w++, p += 4)
                o[w * 4] = (uint8_t)(lut[p[0]] << 6 | lut[p[1]] << 4 | lut[p[2]] << 2 | lut[p[3]]);
        }
        for (; w < A9T_TW; w++)
            o[w * 4] = 0;
    }
}

int a9t_lookup(struct a9t_state *s, uint32_t type, int bin, const uint16_t *pairs, uint8_t *out)
{
    enum a9t_class c = type_class(type);
    int32_t len = cls_bin(s->t, c, bin)[0];
    int32_t n = (int32_t)s->counter;
    int bal = c == A9T_BALANCED;
    int hold = c == A9T_SMOOTH || c == A9T_SPEED || (bal && !s->balanced_full);
    if (!hold) { /* every frame from the table */
        load_lut(s, c, bin, (uint32_t)n);
        render(s, pairs, out);
        n++;
        s->counter = n >= len ? 0 : (uint32_t)n;
        return n < len;
    }
    if (n == 0) { /* frame 0 from the table, cached */
        load_lut(s, c, bin, 0);
        render(s, pairs, out);
        memcpy(s->cache, out, A9T_FRAME_BYTES);
        s->counter = 1;
        return 1;
    }
    int32_t last = bal ? len - 2 : len - 1;
    if (n >= 1 && n < last) {
        memcpy(out, s->cache, A9T_FRAME_BYTES);
    } else if (bal && n == last) { /* Balanced emits two null frames */
        clear_codes(out);
    } else { /* final null frame */
        clear_codes(out);
        s->counter = 0;
        return 0;
    }
    s->counter = (uint32_t)n + 1;
    return 1;
}

/* the one-time init flash, 0x1a7d04..0x1a7df0: uniform frames */
int a9t_init_frame(struct a9t_state *s, int bin, uint8_t *out)
{
    const uint8_t *row = s->t->init + (size_t)bin * A9T_INIT_STRIDE;
    uint32_t len = row[0], n = s->counter;
    uint8_t code = (uint8_t)((row[n + 1] & 3) * 0x55);
    for (int r = 0; r < A9T_TH; r++) {
        uint8_t *o = out + (size_t)r * A9T_TW * 4;
        for (int w = 0; w < A9T_TW; w++)
            o[w * 4] = (r >= A9T_HEAD_ROWS && w < A9T_DATA_WORDS) ? code : 0;
    }
    n++;
    if (n >= len) {
        s->counter = 0;
        s->init_done = 1;
        return 0;
    }
    s->counter = n;
    return 1;
}

/* ---- mode switch (transition handler 0x1a6604..0x1a6e98) ----------------- */
static void req1(struct a9t_request *r, uint32_t code, uint32_t t)
{   /* stur d9/d10 (1,t) at +0xc; strb 0xf at +0x24 */
    r->kind = 1, r->code = code, r->count = 1, r->type[0] = t, r->level[0] = 0xf;
}

static void req4(struct a9t_request *r, uint32_t code, uint32_t t)
{   /* q (4,t,t,t) at +0xc, word t at +0x1c, word 0x0f000000 at +0x24 */
    r->kind = 1, r->code = code, r->count = 4;
    r->type[0] = r->type[1] = r->type[2] = r->type[3] = t;
    r->level[0] = r->level[1] = r->level[2] = 0, r->level[3] = 0xf;
}

static void req5(struct a9t_request *r, uint32_t t1, uint32_t t)
{   /* q (5,t1,t,t) at +0xc, d (t,t) at +0x1c, word 0xf at +0x24, byte 0xf at +0x28 */
    r->count = 5, r->type[0] = t1;
    r->type[1] = r->type[2] = r->type[3] = r->type[4] = t;
    r->level[0] = 0xf, r->level[1] = r->level[2] = r->level[3] = 0, r->level[4] = 0xf;
}

uint32_t a9t_request_build(struct a9t_request *r, uint32_t pre_mode, uint32_t cur_mode,
                           int flag_6c7, int *clear, int *clear_reset)
{
    uint32_t pre = pre_mode & 0xff, cur = cur_mode & 0xff;
    uint32_t mode = cur_mode | (flag_6c7 ? 0 : 0x100);
    r->kind = r->full = r->code = r->count = 0; /* stp xzr, xzr, [S] */
    *clear_reset = 0;
    if (pre == cur && !*clear)
        return mode; /* 0x1a6690: steady update */
    int generic = 0;
    switch (cur) {   /* jump table 0x2ac22 */
    case 1:
        if (pre == 2 || pre == 3) r->kind = 1, r->full = 1, generic = 1;
        else if (pre == 6) req1(r, 2, 6), *clear = 1;
        else if (pre == 9) req1(r, 3, 9), *clear = 1;
        else generic = 1;
        break;
    case 2:
        if (pre == 9) req4(r, 8, 9);
        else if (pre == 6) req4(r, 7, 6);
        else if (pre == 1) {
            r->kind = 1, r->code = 9, r->count = 2, r->type[0] = 1, r->type[1] = 1;
            r->level[0] = 0, r->level[1] = 0xf;
        } else generic = 1;
        break;
    case 3:
        if (pre == 9) req1(r, 3, 9), *clear = 1;
        else if (pre == 6) req1(r, 2, 6), *clear = 1;
        else {
            if (pre == 1) r->kind = 1;
            generic = 1;
        }
        break;
    case 6:
        r->kind = 1;
        if (pre == 2 || pre == 3) r->code = 1, req5(r, 3, 6);
        else if (pre == 1) r->code = 6, req5(r, 1, 6);
        else if (pre == 9) r->code = 5, req5(r, 9, 6);
        else r->kind = 0, generic = 1;
        break;
    case 9:
        r->kind = 1;
        if (pre == 2 || pre == 3) r->code = 0xb, req5(r, 3, 9);
        else if (pre == 1) r->code = 0xc, req5(r, 1, 9);
        else if (pre == 6) r->code = 4, req5(r, 6, 9);
        else r->kind = 0, generic = 1;
        break;
    default:
        generic = 1;
    }
    if (generic && r->kind != 1 && *clear) { /* 0x1a6924: clear request */
        r->kind = 2;
        if (cur == 3) r->count = 1, r->type[0] = 3, r->level[0] = 0xf;
        else if (cur == 9) req5(r, 9, 9);
        else if (cur == 6) req5(r, 6, 6);
    }
    /* generic with kind 1: code is 0 here, so no steps (0x1a68ec..0x1a6900) */
    *clear_reset = *clear != 0; /* 0x1a6e98 */
    return mode;
}

static void fill(uint16_t *a, uint8_t from, uint8_t to)
{
    uint16_t v = (uint16_t)(from << 4 | to);
    for (size_t i = 0; i < A9T_N; i++)
        a[i] = v;
}

/* 0x1a6ab0..0x1a6e78 (jump table 0x2ac37 on count - 1) */
void a9t_request_arrays(const struct a9t_request *r, uint8_t *old, uint16_t *const arr[5])
{
    const uint8_t *l = r->level;
    uint8_t to;
    if (r->count < 1 || r->count > 5)
        return;
    uint8_t first = r->count == 1 ? 0xf : l[0];
    for (size_t i = 0; i < A9T_N; i++)
        arr[0][i] = (uint16_t)(old[i] << 4 | first);
    switch (r->count) {
    case 1: to = 0xf; break;
    case 2: fill(arr[1], l[0], l[1]); to = l[1]; break;
    case 3: fill(arr[1], l[0], l[1]); fill(arr[2], l[1], l[2]); to = l[2]; break;
    case 4:
        fill(arr[1], l[0], l[1]); fill(arr[2], l[1], l[2]); fill(arr[3], l[2], l[3]);
        to = l[3];
        break;
    default: /* 5: stock writes S+0x48 twice and never S+0x50 (MEASURED) */
        fill(arr[1], l[0], l[1]); fill(arr[2], l[1], l[2]); fill(arr[3], l[3], l[4]);
        to = l[4];
        break;
    }
    memset(old, to, A9T_N);
}

/* frame dispatch 0x1a7c78..0x1a81c4 (jump table 0x2ac4e on the step index) */
void a9t_update_begin(struct a9t_state *s)
{
    s->step = 0; /* 0x1a7abc */
}

int a9t_update_frame(struct a9t_state *s, const struct a9t_request *r, uint16_t *const arr[5],
                     uint32_t type, int bin, const uint16_t *pairs, uint8_t *out)
{
    uint32_t count = r ? r->count : 0;
    if (r)
        s->balanced_full = r->full;
    if (count == 0 || s->step >= count)
        return a9t_lookup(s, type, bin, pairs, out);
    if (s->step > 4) { /* unreachable with count <= 5 */
        s->step++;
        return 1;
    }
    if (!a9t_lookup(s, r->type[s->step], bin, arr[s->step], out))
        s->step++;
    return 1;
}
