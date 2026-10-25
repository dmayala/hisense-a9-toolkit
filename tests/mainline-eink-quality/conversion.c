/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L
#include "a9_eink_engine.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "reference.inc"

/* Experimental cache-local transpose. Kept in the host benchmark until the
 * fixture and relative CPU cost justify promoting it into the driver. */
static void tiled(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
                  int c1, int r1, int c2, int r2, struct a9e_rect *changed)
{
    int bpp = fmt == A9E_FMT_R8 ? 1 : 4;
    int i0 = fmt == A9E_FMT_BGRX ? 2 : 0, i2 = fmt == A9E_FMT_BGRX ? 0 : 2;
    struct a9e_rect ch = {A9T_W, A9T_H, 0, 0};
    uint8_t tile[32][32];
    if (c1 < 0) c1 = 0;
    if (r1 < 0) r1 = 0;
    if (c2 > A9E_FB_W) c2 = A9E_FB_W;
    if (r2 > A9E_FB_H) r2 = A9E_FB_H;
    for (int rb = r1; rb < r2; rb += 32) {
        int re = rb + 32 < r2 ? rb + 32 : r2;
        for (int cb = c1; cb < c2; cb += 32) {
            int ce = cb + 32 < c2 ? cb + 32 : c2;
            for (int r = rb; r < re; r++) {
                const uint8_t *p = src + (size_t)r * pitch + (size_t)cb * bpp;
                for (int c = cb; c < ce; c++, p += bpp)
                    tile[c-cb][r-rb] = bpp == 1 ? p[0] :
                        (uint8_t)((76u*p[i0] + 150u*p[1] + 30u*p[i2]) >> 8);
            }
            for (int c = cb; c < ce; c++) {
                int y = A9E_FB_W - 1 - c;
                uint8_t *o = grey + (size_t)y * A9T_W;
                for (int r = rb; r < re; r++) {
                    uint8_t v = tile[c-cb][r-rb];
                    if (changed && o[r] != v) {
                        if (r < ch.x1) ch.x1 = r;
                        if (r + 1 > ch.x2) ch.x2 = r + 1;
                        if (y < ch.y1) ch.y1 = y;
                        if (y + 1 > ch.y2) ch.y2 = y + 1;
                    }
                    o[r] = v;
                }
            }
        }
    }
    if (changed) a9e_rect_union(changed, &ch);
}

static unsigned rng = 7;
static uint8_t random_byte(void) { rng = rng * 1664525u + 1013904223u; return (uint8_t)(rng >> 24); }
static double ms(void)
{
    struct timespec t;
    assert(!clock_gettime(CLOCK_MONOTONIC, &t));
    return t.tv_sec * 1000.0 + t.tv_nsec / 1e6;
}
static int cmp(const void *a, const void *b) { double x=*(const double *)a,y=*(const double *)b; return (x>y)-(x<y); }
typedef void (*convert)(uint8_t *, const uint8_t *, uint32_t, enum a9e_fmt, int, int, int, int, struct a9e_rect *);
static void bench_pair(uint8_t *a, uint8_t *b, uint8_t *src, unsigned pitch, enum a9e_fmt fmt, int changing)
{
    double times[2][31];
    convert funcs[2] = {reference, tiled};
    uint8_t *grey[2] = {a,b};
    for (int k=0; k<34; k++) for(int order=0;order<2;order++) {
        int which=(k+order)&1; /* alternate order to reduce host-load bias */
        if (changing) memset(grey[which], k & 1 ? 0 : 255, A9T_N);
        struct a9e_rect ch = {0};
        double t = ms();
        funcs[which](grey[which], src, pitch, fmt, 0, 0, A9E_FB_W, A9E_FB_H, &ch);
        if (k>=3) times[which][k-3] = ms()-t;
    }
    for(int i=0;i<2;i++) qsort(times[i], 31, sizeof(double), cmp);
    printf("conversion fmt=%d pitch=%u changed=%d original_ms=%.3f tiled_ms=%.3f speedup=%.2f original_p90=%.3f tiled_p90=%.3f\n",
           fmt,pitch,changing,times[0][15],times[1][15],times[0][15]/times[1][15],times[0][27],times[1][27]);
}

int main(void)
{
    uint8_t *src = malloc((size_t)(A9E_FB_W*4+97)*A9E_FB_H+128);
    uint8_t *a = malloc(A9T_N+128), *b = malloc(A9T_N+128), *c = malloc(A9T_N+128);
    struct a9t_tables *tables = calloc(1,sizeof(*tables));
    struct a9t_state st;
    struct a9t_work *work = malloc(sizeof(*work));
    uint8_t *cache = malloc(A9T_FRAME_BYTES);
    assert(src && a && b && c && tables && work && cache);
    for(size_t i=0;i<(size_t)(A9E_FB_W*4+97)*A9E_FB_H+128;i++) src[i]=random_byte();
    const int rects[][4]={{0,0,824,1648},{-8,-7,830,1655},{3,5,61,92},{823,1647,824,1648},
                            {13,15,14,16},{0,0,0,0},{21,17,20,30},{5,30,17,29}};
    unsigned cases=0;
    for (int fmt=0;fmt<3;fmt++) for(int pad=0;pad<3;pad++) {
        unsigned pitch=(A9E_FB_W+(pad==1?8:pad==2?17:0))*(fmt==A9E_FMT_R8?1:4);
        for(unsigned r=0;r<sizeof(rects)/sizeof(rects[0]);r++) for(int diff=0;diff<4;diff++) {
            memset(a,0xa5,A9T_N+128); memcpy(b,a,A9T_N+128); memcpy(c,a,A9T_N+128);
            struct a9e_rect ca=diff==0 ? (struct a9e_rect){0} : (struct a9e_rect){2,3,4,5},cb=ca,cc=ca;
            const int *v=rects[r];
            if(diff==1 || diff==2) {
                reference(a+64,src+3,pitch,fmt,0,0,824,1648,NULL);
                memcpy(b,a,A9T_N+128); memcpy(c,a,A9T_N+128);
                if(diff==2) a[64+30*A9T_W+70]^=0xff,b[64+30*A9T_W+70]^=0xff,c[64+30*A9T_W+70]^=0xff;
            }
            reference(a+64,src+3,pitch,fmt,v[0],v[1],v[2],v[3],diff==3?NULL:&ca);
            tiled(b+64,src+3,pitch,fmt,v[0],v[1],v[2],v[3],diff==3?NULL:&cb);
            a9e_fb_to_grey(c+64,src+3,pitch,fmt,v[0],v[1],v[2],v[3],diff==3?NULL:&cc);
            assert(!memcmp(a,b,A9T_N+128) && !memcmp(a,c,A9T_N+128));
            assert(!memcmp(&ca,&cb,sizeof(ca)) && !memcmp(&ca,&cc,sizeof(ca)));
            cases++;
        }
        for(int changing=0;changing<2;changing++)
            bench_pair(a,b,src,pitch,fmt,changing);
    }
    a9t_init(&st,tables,cache);
    for(size_t i=0;i<A9T_N;i++) a[i]=random_byte();
    for(unsigned mode=513;mode<=518;mode++) if(mode==513 || mode==515 || mode==518) {
        double times[31];
        for(int i=0;i<31;i++) { double t=ms(); a9t_condition(&st,a,mode,b,work); times[i]=ms()-t; }
        qsort(times,31,sizeof(double),cmp);
        printf("condition mode=%u median_ms=%.3f\n",mode,times[15]);
    }
    printf("PASS conversion: %u byte/changed/canary comparisons; medians of 31 warm host samples\n",cases);
    free(src);free(a);free(b);free(c);free(tables);free(work);free(cache);
}
