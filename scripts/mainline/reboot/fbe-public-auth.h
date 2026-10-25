// SPDX-License-Identifier: Apache-2.0
// Bounded, allocation-free inspection of public QCOM KMKD characteristics.
// Opaque values are skipped; no cryptographic validity is implied by parsing.
#ifndef A9_FBE_PUBLIC_AUTH_H
#define A9_FBE_PUBLIC_AUTH_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>
struct fbe_auth { unsigned aes, gcm, decrypt, os, patch, vendor, boot, boot_present; };
struct fbe_cbor { const uint8_t *p, *end; unsigned budget; };
static int fbe_head(struct fbe_cbor *c, unsigned *major, uint64_t *n) {
    if (!c->budget || c->p == c->end) return -1;
    --c->budget;
    unsigned h = *c->p++, ai = h & 31; *major = h >> 5;
    if (ai < 24) { *n = ai; return 0; }
    if (ai > 27) return -1;
    size_t bytes = (size_t)1 << (ai - 24);
    if (bytes > (size_t)(c->end-c->p)) return -1;
    *n = 0;
    while (bytes--) *n = (*n << 8) | *c->p++;
    return 0;
}
static int fbe_skip(struct fbe_cbor *c, unsigned depth) {
    unsigned m; uint64_t n;
    if (depth > 8 || fbe_head(c, &m, &n)) return -1;
    if (m < 2 || m == 7) return 0;
    if (m == 2 || m == 3) {
        if (n > (uint64_t)(c->end-c->p)) return -1;
        c->p += (size_t)n; return 0;
    }
    if (m != 4 && m != 5) return -1;
    if (n > 128) return -1;
    n *= m == 5 ? 2 : 1;
    while (n--) if (fbe_skip(c, depth+1)) return -1;
    return 0;
}
static int fbe_uint(struct fbe_cbor *c, uint64_t *n) {
    unsigned m;
    return fbe_head(c, &m, n) || m != 0 ? -1 : 0;
}
static int fbe_auth_map(struct fbe_cbor *c, struct fbe_auth *a) {
    unsigned m; uint64_t count, tag, n;
    if (fbe_head(c, &m, &count) || m != 5 || count > 128) return -1;
    uint64_t seen[128]; unsigned used = 0;
    while (count--) {
        if (fbe_uint(c, &tag)) return -1;
        for (unsigned i = 0; i < used; ++i) if (seen[i] == tag) return -1;
        seen[used++] = tag;
        if (tag == 0x20000004U || tag == 0x20000001U) {
            if (fbe_head(c, &m, &n) || m != 4 || n > 64) return -1;
            while (n--) {
                uint64_t value;
                if (fbe_uint(c, &value)) return -1;
                if (tag == 0x20000004U && value == 32) a->gcm = 1;
                if (tag == 0x20000001U && value == 1) a->decrypt = 1;
            }
        } else if (tag == 0x10000002U || tag == 0x300002c1U ||
                   tag == 0x300002c2U || tag == 0x300002ceU || tag == 0x300002cfU) {
            if (fbe_uint(c, &n) || n > 99999999U) return -1;
            if (tag == 0x10000002U) a->aes = n == 32;
            if (tag == 0x300002c1U) a->os = (unsigned)n;
            if (tag == 0x300002c2U) a->patch = (unsigned)n;
            if (tag == 0x300002ceU) a->vendor = (unsigned)n;
            if (tag == 0x300002cfU) { a->boot = (unsigned)n; a->boot_present = 1; }
        } else if (fbe_skip(c, 0)) return -1;
    }
    return 0;
}
static int fbe_public_auth(const uint8_t *p, size_t size, struct fbe_auth *a) {
    memset(a, 0, sizeof(*a));
    if (!p || !size || size > 65536) return -33;
    struct fbe_cbor c = {p, p+size, 1024};
    unsigned m; uint64_t n;
    if (fbe_head(&c, &m, &n) || m != 4 || n != 5 ||
        fbe_uint(&c, &n) || n != 0x4b4d4b44 ||
        fbe_skip(&c, 0) || fbe_skip(&c, 0) ||
        fbe_head(&c, &m, &n) || m != 2 || n > (uint64_t)(c.end-c.p)) return -33;
    struct fbe_cbor fields = {c.p, c.p+(size_t)n, 1024};
    c.p += (size_t)n;
    if (fbe_skip(&c, 0) || c.p != c.end ||
        fbe_head(&fields, &m, &n) || m != 5 || n > 2) return -33;
    unsigned seen = 0;
    struct fbe_auth parsed = {0};
    while (n--) {
        uint64_t label;
        if (fbe_uint(&fields, &label) || label > 1 || (seen & (1U << label))) return -33;
        seen |= 1U << label;
        if (label == 1) { if (fbe_auth_map(&fields, &parsed)) return -33; }
        else if (fbe_skip(&fields, 0)) return -33;
    }
    if (!(seen & 2) || fields.p != fields.end) return -33;
    *a = parsed;
    return 0;
}
#endif
