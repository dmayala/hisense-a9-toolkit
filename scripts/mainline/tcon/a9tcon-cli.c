/* a9tcon CLI: one TCON update (or the init flash) from raw files, frames out.
 *
 *   a9tcon COMPOSER [--grey F | --rgba F] [--old F] [--mode 0x201] [--clear 0|1]
 *          [--t1 N] [--t2 N] [--pre MODE [--flag6c7 0|1]] [--full 0|1]
 *          [--controls B,W,C] [--template F] [--init] [--frames OUT] [--levels OUT]
 *          [--pairs OUT] [-v]
 *
 * --grey  1648x824 grey, row-major (default: all white 255)
 * --rgba  832x1648 RGBA portrait buffer (runs the grey + rotation stage)
 * --old   1648x824 old levels 0..15 (default: all 15)
 * --pre   previous mode: runs the mode-switch builder (steps before the update).
 *         Without it the update is a steady one, exactly like a9tcon.py.
 * --full  request S+4 for a steady update (Balanced: full table instead of hold)
 * --template  the control template (hisense/a9-ctrl-template.bin with its A9CT
 *         header, or a raw 1,485,568-byte template; a9t-template-file.h).
 *         Every output frame and the hold cache start as the template, so the
 *         frames are whole 32-bit words exactly as the panel gets them (bytes
 *         1..3 = the control template, byte 0 = drive data; null frames = the
 *         template), and the printed sha256 covers them. Without it, bytes
 *         1..3 of every frame are zero (byte 0 only is meaningful; the panel is
 *         not clocked by such frames).
 * --init  emit the one-time init flash at the bin of t1/t2 instead of an update
 * Prints type, bin, step list, frame count, sha256 of every frame (-v) and of
 * all frames concatenated (last line: "frames N sha256 X").
 */
#include "a9tcon.h"
#include "a9t-template-file.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc((size_t)n ? (size_t)n : 1);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "%s: read failed\n", path);
        exit(2);
    }
    fclose(f);
    *len = (size_t)n;
    return b;
}

static void read_exact(const char *path, uint8_t *dst, size_t want)
{
    size_t n;
    uint8_t *b = slurp(path, &n);
    if (n != want) {
        fprintf(stderr, "%s: %zu bytes, expected %zu\n", path, n, want);
        exit(2);
    }
    memcpy(dst, b, want);
    free(b);
}

static void dump(const char *path, const void *p, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(p, 1, n, f) != n || fclose(f)) {
        perror(path);
        exit(2);
    }
}

static void hexout(const uint8_t d[32], int n)
{
    for (int i = 0; i < n; i++)
        printf("%02x", d[i]);
}

struct sink {
    FILE *f;
    const char *path;
    struct a9t_sha256_ctx all;
    int n, verbose;
};

static void emit(struct sink *k, const uint8_t *out)
{
    uint8_t d[32];
    a9t_sha256_update(&k->all, out, A9T_FRAME_BYTES);
    if (k->verbose) {
        a9t_sha256(out, A9T_FRAME_BYTES, d);
        printf("frame %d ", k->n);
        hexout(d, 32);
        printf("\n");
    }
    if (k->f && fwrite(out, 1, A9T_FRAME_BYTES, k->f) != A9T_FRAME_BYTES) {
        perror(k->path);
        exit(2);
    }
    k->n++;
}

int main(int argc, char **argv)
{
    const char *tmpl_p = 0, *grey_p = 0, *rgba_p = 0, *old_p = 0, *frames_p = 0, *levels_p = 0, *pairs_p = 0;
    uint32_t mode = 0x201, pre = 0;
    int clear = 0, t1 = 25, t2 = 25, have_pre = 0, flag6c7 = 1, full = 0, init = 0, verbose = 0;
    int cb = 30, cw = 30, cc = 0;
    if (argc < 2) {
        fprintf(stderr,
                "usage: %s COMPOSER [--grey F | --rgba F] [--old F] [--mode M] [--clear 0|1]\n"
                "         [--t1 N] [--t2 N] [--pre M [--flag6c7 0|1]] [--full 0|1] [--controls B,W,C]\n"
                "         [--template F] [--init] [--frames OUT] [--levels OUT] [--pairs OUT] [-v]\n"
                "  --template F: frames carry the control template in bytes 1..3 (else zero)\n",
                argv[0]);
        return 2;
    }
    for (int i = 2; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : 0;
#define OPT(name) (!strcmp(a, name) && v && (i++, 1))
        if (OPT("--grey")) grey_p = v;
        else if (OPT("--rgba")) rgba_p = v;
        else if (OPT("--old")) old_p = v;
        else if (OPT("--mode")) mode = (uint32_t)strtoul(v, 0, 0);
        else if (OPT("--clear")) clear = atoi(v);
        else if (OPT("--t1")) t1 = (int)strtol(v, 0, 0);
        else if (OPT("--t2")) t2 = (int)strtol(v, 0, 0);
        else if (OPT("--pre")) pre = (uint32_t)strtoul(v, 0, 0), have_pre = 1;
        else if (OPT("--flag6c7")) flag6c7 = atoi(v);
        else if (OPT("--full")) full = atoi(v);
        else if (OPT("--controls")) sscanf(v, "%d,%d,%d", &cb, &cw, &cc);
        else if (OPT("--template")) tmpl_p = v;
        else if (OPT("--frames")) frames_p = v;
        else if (OPT("--levels")) levels_p = v;
        else if (OPT("--pairs")) pairs_p = v;
        else if (!strcmp(a, "--init")) init = 1;
        else if (!strcmp(a, "-v")) verbose = 1;
        else {
            fprintf(stderr, "unknown option %s\n", a);
            return 2;
        }
#undef OPT
    }

    size_t blen;
    uint8_t *bin = slurp(argv[1], &blen);
    struct a9t_tables *tab = malloc(sizeof(*tab));
    if (!tab || a9t_load(tab, bin, blen)) {
        fprintf(stderr, "%s: unsupported composer (size or sha256 mismatch)\n", argv[1]);
        return 1;
    }
    free(bin);

    uint8_t *cache = malloc(A9T_FRAME_BYTES), *out = malloc(A9T_FRAME_BYTES);
    uint8_t *tmpl = calloc(1, A9T_FRAME_BYTES); /* no --template: all zero (old behaviour) */
    if (tmpl_p) {
        char th[65];
        if (a9t_template_read(tmpl_p, tmpl, th))
            return 1;
        printf("template %s%s\n", th, strcmp(th, A9T_CT_SHA256) ? " (NOT the kernel's)" : "");
    }
    uint8_t *grey = malloc(A9T_N), *old = malloc(A9T_N), *lv = malloc(A9T_N);
    uint16_t *pairs = malloc(A9T_N * 2), *arr[5];
    struct a9t_work *work = malloc(sizeof(*work));
    for (int k = 0; k < 5; k++)
        arr[k] = calloc(A9T_N, 2); /* stock: zeroed once at TCON start */
    struct a9t_state st;
    a9t_init(&st, tab, cache);
    memcpy(cache, tmpl, A9T_FRAME_BYTES); /* a9t_init zeroes it */
    a9t_set_controls(&st, cb, cw, cc);
    int bin_ = a9t_temp_bin(&st, t1, t2);

    struct sink sk;
    memset(&sk, 0, sizeof(sk));
    sk.f = frames_p ? fopen(frames_p, "wb") : 0;
    sk.path = frames_p;
    sk.verbose = verbose;
    if (frames_p && !sk.f) {
        perror(frames_p);
        return 2;
    }
    a9t_sha256_init(&sk.all);
    uint8_t d[32];
    int more;

    if (init) {
        printf("init flash bin %d\n", bin_);
        do {
            memcpy(out, tmpl, A9T_FRAME_BYTES); /* a fresh ring buffer slot */
            more = a9t_init_frame(&st, bin_, out);
            emit(&sk, out);
        } while (more);
        goto done;
    }

    if (rgba_p) {
        uint8_t *rgba = malloc(A9T_SRC_BYTES);
        read_exact(rgba_p, rgba, A9T_SRC_BYTES);
        a9t_rgba_to_grey(rgba, grey);
        free(rgba);
    } else if (grey_p) {
        read_exact(grey_p, grey, A9T_N);
    } else {
        memset(grey, 255, A9T_N);
    }
    if (old_p)
        read_exact(old_p, old, A9T_N);
    else
        memset(old, 15, A9T_N);

    struct a9t_request req;
    memset(&req, 0, sizeof(req));
    int clear_reset = 0;
    if (have_pre) {
        mode = a9t_request_build(&req, pre, mode, flag6c7, &clear, &clear_reset);
        a9t_request_arrays(&req, old, arr);
    } else {
        req.full = (uint32_t)full;
    }
    a9t_condition(&st, grey, mode, lv, work);
    a9t_pack(old, lv, mode, clear, pairs);
    int type = a9t_final_type(mode, clear);
    printf("mode %#x clear %d type %d bin %d steps [", mode, clear, type, bin_);
    for (uint32_t k = 0; k < req.count; k++)
        printf("%s%u", k ? "," : "", req.type[k]);
    printf("] kind %u full %u clear_reset %d\n", req.kind, req.full, clear_reset);
    a9t_sha256(lv, A9T_N, d);
    printf("levels ");
    hexout(d, 8);
    a9t_sha256((const uint8_t *)pairs, A9T_N * 2, d);
    printf(" pairs ");
    hexout(d, 8);
    printf("\n");
    if (levels_p)
        dump(levels_p, lv, A9T_N);
    if (pairs_p)
        dump(pairs_p, pairs, A9T_N * 2);

    a9t_update_begin(&st);
    do {
        memcpy(out, tmpl, A9T_FRAME_BYTES); /* a fresh ring buffer slot */
        more = a9t_update_frame(&st, &req, arr, (uint32_t)type, bin_, pairs, out);
        emit(&sk, out);
    } while (more);

done:
    a9t_sha256_final(&sk.all, d);
    printf("frames %d sha256 ", sk.n);
    hexout(d, 32);
    printf("\n");
    if (sk.f && fclose(sk.f)) {
        perror(frames_p);
        return 2;
    }
    return 0;
}
