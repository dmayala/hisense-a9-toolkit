/* a9tcon: standalone software TCON for the Hisense A9 (HLTE556N) E Ink panel.
 *
 * C port of scripts/mainline/a9tcon.py, extended with the mode-switch step
 * sequences and the RGBA -> grey + rotation stage. Checked byte for byte against
 * the stock composer code running under Unicorn (scripts/mainline/tcon-oracle.py,
 * test: scripts/mainline/tcon/test-tcon-c.py). docs/mainline-plan.md section 4.
 *
 * This file and a9tcon.c carry NO vendor data. The waveform tables, the
 * temperature bin map, the init flash and the 32x32 dither matrix are copied at
 * run time from the user's own composer binary by a9t_load() (a9tcon-load.c).
 *
 * Core (a9tcon.c): C11, freestanding-friendly. No allocation, no libc beyond
 * memcpy/memset; every buffer is passed in by the caller. Integer only.
 *
 * Geometry: the TCON works on a landscape 1648 x 824 grey/level buffer
 * (row-major, 1648 bytes per row). A drive frame is 448 x 829 words of 4 bytes
 * (XRGB8888 on the DSI link); only byte 0 of words 0..411 of rows 5..828 carries
 * data (4 pixels, 2 bits each).
 *
 * Bytes 1..3 of every word are NOT zero on the panel: they carry the panel's
 * control signals (byte 1 gate/source strobes, byte 2 0x00, byte 3 0xff), a
 * fixed CONTROL TEMPLATE that stock builds once at TCON start (composer
 * HWCDisplay::SetPowerMode 0x19eed0, librecovery_ui.so Init_Eink_SWTcon) and
 * never overwrites (MEASURED, 16 live stock ring buffers, 2026-10-15). The core
 * writes byte 0 only, never bytes 1..3, so every output buffer (and the hold
 * cache) must be pre-filled with the template by the caller; a "null" frame is
 * then the template itself. The template is vendor-derived and loaded at run
 * time (scripts/mainline/eink/tools), never part of this code.
 */
#ifndef A9TCON_H
#define A9TCON_H

#include <stddef.h>
#include <stdint.h>

#define A9T_W 1648
#define A9T_H 824
#define A9T_N (A9T_W * A9T_H)
#define A9T_TW 448
#define A9T_TH 829
#define A9T_FRAME_BYTES (A9T_TW * A9T_TH * 4) /* 1,485,568 */
#define A9T_HEAD_ROWS 5
#define A9T_DATA_WORDS (A9T_W / 4) /* 412 */

/* SurfaceFlinger's portrait buffer: 1648 rows of 832 RGBA pixels (824 used). */
#define A9T_SRC_STRIDE 832
#define A9T_SRC_ROWS 1648
#define A9T_SRC_BYTES (A9T_SRC_STRIDE * A9T_SRC_ROWS * 4)

#define A9T_BINS 14
#define A9T_SLOTS 257

/* Waveform classes (lookUpTable jump table): */
enum a9t_class { A9T_CLEAR3, A9T_TYPE2, A9T_BALANCED, A9T_SMOOTH, A9T_SPEED, A9T_NCLASS };

/* Tables lifted from the composer. Raw layout as in the binary: per class,
 * A9T_BINS bins of 257 rows; row = [length][code per frame ...]. */
#define A9T_RS_CLEAR 0x6a
#define A9T_RS_BAL 0x38
#define A9T_RS_SMOOTH 0x1e
#define A9T_INIT_STRIDE 0x78

struct a9t_tables {
    uint8_t bin_map[51];
    uint8_t dither[32 * 32];
    uint8_t init[A9T_BINS * A9T_INIT_STRIDE];
    uint8_t clear3[A9T_BINS * A9T_SLOTS * A9T_RS_CLEAR];
    uint8_t type2[A9T_BINS * A9T_SLOTS * A9T_RS_CLEAR];
    uint8_t balanced[A9T_BINS * A9T_SLOTS * A9T_RS_BAL];
    uint8_t smooth[A9T_BINS * A9T_SLOTS * A9T_RS_SMOOTH];
    uint8_t speed[A9T_BINS * A9T_SLOTS * A9T_RS_SMOOTH];
};

/* Mode-switch request (the stock object at this+0x19e7418, "S"). */
struct a9t_request {
    uint32_t kind;     /* +0x00: 0 none, 1 mode change, 2 clear request */
    uint32_t full;     /* +0x04: 1 = Balanced uses the full table (no hold) */
    uint32_t code;     /* +0x08: transition code */
    uint32_t count;    /* +0x0c: number of steps (0..5) */
    uint32_t type[5];  /* +0x10..+0x20: waveform type per step */
    uint8_t level[5];  /* +0x24..+0x28: target levels of the steps */
};

/* Engine state (globals in the stock binary). */
struct a9t_state {
    const struct a9t_tables *t;
    uint8_t g2[256], g16[256];
    uint8_t g16s[256];          /* Clear UI shadow curve then g16 (mainline policy) */
    int clear_shadow;           /* 1 = Clear (low byte 3) uses g16s; default 0 */
    uint8_t scratch[A9T_SLOTS]; /* 0x4c86d9; slot 256 survives 256-slot loads */
    uint32_t counter;           /* 0x4c7fbc, frame index inside one table pass */
    uint32_t step;              /* 0x4c86d4, step index of a mode switch */
    int init_done;              /* 0x4c7fb8 bit 0 */
    uint32_t balanced_full;     /* request S+4, read by lookUpTable */
    uint8_t *cache;             /* A9T_FRAME_BYTES, frame 0 of a hold pass */
};

/* Dither working rows (rolling, 4 x 1648 int32). */
struct a9t_work {
    int32_t row[4][A9T_W];
};

#ifdef __cplusplus
extern "C" {
#endif

/* --- loader (a9tcon-load.c; hosted: needs only memcpy) ------------------- */
#define A9T_COMPOSER_SHA256 "8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed"
/* Returns 0 on success, -1 on a size or sha256 mismatch. */
int a9t_load(struct a9t_tables *t, const uint8_t *composer, size_t len);
/* sizes for FFI users: 0 tables, 1 state, 2 work, 3 request */
size_t a9t_sizeof(int what);
void a9t_sha256(const uint8_t *data, size_t len, uint8_t out[32]);
struct a9t_sha256_ctx {
    uint32_t h[8];
    uint8_t buf[64];
    size_t n;
    uint64_t total;
};
void a9t_sha256_init(struct a9t_sha256_ctx *c);
void a9t_sha256_update(struct a9t_sha256_ctx *c, const uint8_t *p, size_t len);
void a9t_sha256_final(struct a9t_sha256_ctx *c, uint8_t out[32]);

/* --- core ------------------------------------------------------------------ */
void a9t_init(struct a9t_state *s, const struct a9t_tables *t, uint8_t *cache);
void a9t_set_controls(struct a9t_state *s, int32_t black, int32_t white, int32_t contrast);
uint8_t a9t_clear_ui_grey(uint8_t x);
void a9t_set_clear_shadow(struct a9t_state *s, int on);
int a9t_temp_bin(const struct a9t_state *s, int32_t t1, int32_t t2);

/* RGBA (A9T_SRC_BYTES, portrait) -> landscape grey (A9T_N). */
void a9t_rgba_to_grey(const uint8_t *rgba, uint8_t *grey);

/* grey (A9T_N) -> target levels 0..15 (A9T_N). */
void a9t_condition(const struct a9t_state *s, const uint8_t *grey, uint32_t mode,
                   uint8_t *levels, struct a9t_work *w);
/* (old, new) levels -> uint16 index (old<<4|new, or 256 = cleanup sentinel). */
void a9t_pack(const uint8_t *old, const uint8_t *new_, uint32_t mode, int clear, uint16_t *pairs);
int a9t_final_type(uint32_t mode, int clear);

/* One lookUpTable call: fills out (A9T_FRAME_BYTES). Returns 1 = more frames. */
int a9t_lookup(struct a9t_state *s, uint32_t type, int bin, const uint16_t *pairs, uint8_t *out);
/* One frame of the one-time init flash. Returns 1 = more frames. */
int a9t_init_frame(struct a9t_state *s, int bin, uint8_t *out);

/* Mode switch. a9t_request_build: the transition handler, run once per update
 * with the previous mode's low byte and the new mode word. *clear is the
 * update's clear flag (may be set); *clear_reset is set when the clear flag must
 * be cleared after this update. Returns the mode word the update runs with.
 * flag_6c7 = the engine byte this+0x6c7 (0 adds 0x100 to the mode word). */
uint32_t a9t_request_build(struct a9t_request *r, uint32_t pre_mode, uint32_t cur_mode,
                           int flag_6c7, int *clear, int *clear_reset);
/* Fill the step index arrays (5 x A9T_N uint16, persistent between updates)
 * from old levels, then set old := the last step's level. */
void a9t_request_arrays(const struct a9t_request *r, uint8_t *old, uint16_t *const arr[5]);
/* One frame of an update with steps: the steps, then the final pass
 * (type, pairs). Call a9t_update_begin() once before the first frame. */
void a9t_update_begin(struct a9t_state *s);
int a9t_update_frame(struct a9t_state *s, const struct a9t_request *r, uint16_t *const arr[5],
                     uint32_t type, int bin, const uint16_t *pairs, uint8_t *out);

#ifdef __cplusplus
}
#endif
#endif
