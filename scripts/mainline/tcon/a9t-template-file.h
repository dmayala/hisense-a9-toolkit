/* Host tools only (a9tcon CLI, eink/test/a9e-test): read the A9 control
 * template (a9tcon.h, "Bytes 1..3") from a file. Carries NO vendor data.
 *
 * Accepted:
 *  - the firmware file hisense/a9-ctrl-template.bin: little endian 80-byte
 *    header {u32 magic "A9CT" (0x54433941), u32 version 1, u32 header_size 80,
 *    u32 payload_size A9T_FRAME_BYTES, u8 source_sha256[32],
 *    u8 payload_sha256[32]} then the payload; the payload sha256 must match;
 *  - a raw template of exactly A9T_FRAME_BYTES (e.g. the Unicorn extraction).
 * Either way every word must have byte 0 = 0, byte 2 = 0, byte 3 = 0xff (the
 * same acceptance as the kernel, patch 0028, minus the compiled-in hash: the
 * hash is printed so the caller can compare it).
 */
#ifndef A9T_TEMPLATE_FILE_H
#define A9T_TEMPLATE_FILE_H

#include "a9tcon.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define A9T_CT_MAGIC 0x54433941u /* "A9CT" */
#define A9T_CT_HEADER 80u
/* sha256 of the payload the kernel accepts (composer 8c89557b..., SetPowerMode) */
#define A9T_CT_SHA256 "a88ae8efc5325e1ab52e4e48b73d2abb1a3c81a7989baa2a226513b07cebfbe7"

static inline uint32_t a9t_ct_rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* Fills out (A9T_FRAME_BYTES) and hex (65 bytes: payload sha256). Returns 0,
 * or prints why to stderr and returns -1. */
static inline int a9t_template_read(const char *path, uint8_t *out, char hex[65])
{
    FILE *f = fopen(path, "rb");
    uint8_t *b, d[32];
    long n;
    size_t len, off;
    if (!f) {
        perror(path);
        return -1;
    }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0 || !(b = malloc((size_t)n)) || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "%s: read failed\n", path);
        fclose(f);
        return -1;
    }
    fclose(f);
    len = (size_t)n;
    if (len >= 4 && a9t_ct_rd32(b) == A9T_CT_MAGIC) {
        if (len != A9T_CT_HEADER + A9T_FRAME_BYTES || a9t_ct_rd32(b + 4) != 1 ||
            a9t_ct_rd32(b + 8) != A9T_CT_HEADER || a9t_ct_rd32(b + 12) != A9T_FRAME_BYTES) {
            fprintf(stderr, "%s: A9CT header: bad version/size fields or file size %zu\n", path, len);
            free(b);
            return -1;
        }
        off = A9T_CT_HEADER;
        a9t_sha256(b + off, A9T_FRAME_BYTES, d);
        if (memcmp(d, b + 48, 32)) {
            fprintf(stderr, "%s: payload sha256 does not match the header\n", path);
            free(b);
            return -1;
        }
    } else if (len == A9T_FRAME_BYTES) {
        off = 0;
        a9t_sha256(b, A9T_FRAME_BYTES, d);
    } else {
        fprintf(stderr, "%s: neither an A9CT file nor a raw %d-byte template\n", path,
                A9T_FRAME_BYTES);
        free(b);
        return -1;
    }
    for (size_t i = 0; i < A9T_FRAME_BYTES; i += 4) {
        const uint8_t *w = b + off + i;
        if (w[0] || w[2] || w[3] != 0xff) {
            fprintf(stderr, "%s: word %zu = %02x %02x %02x %02x: not a control template\n",
                    path, i / 4, w[0], w[1], w[2], w[3]);
            free(b);
            return -1;
        }
    }
    memcpy(out, b + off, A9T_FRAME_BYTES);
    for (int i = 0; i < 32; i++)
        snprintf(hex + 2 * i, 3, "%02x", d[i]);
    free(b);
    return 0;
}

#endif
