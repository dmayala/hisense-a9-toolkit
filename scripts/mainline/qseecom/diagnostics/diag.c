/* SPDX-License-Identifier: Apache-2.0 */
#include "diag.h"
#include "schema.h"
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

void diag_increment(uint32_t *n) { if (*n != UINT32_MAX) ++*n; }

static int numeric(const char *p, char type)
{
    char *end;
    unsigned long long u;
    long long i;
    if (!*p) return 0;
    if (type == 'A') return !strcmp(p, "0x83");
    if (type == 'H') {
        static const char *ids[] = {"0x32000201", "0x32000206", "0x72000201", "0x72000206",
                                   "0xb2000201", "0xb2000206", "0xf2000201", "0xf2000206"};
        for (size_t j = 0; j < sizeof(ids)/sizeof(ids[0]); ++j)
            if (!strcmp(p, ids[j])) return 1;
        return 0;
    }
    const char *q = p;
    if ((type == 'I' || type == 'S') && *q == '-') ++q;
    if (!*q) return 0;
    for (const char *r = q; *r; ++r) if (*r < '0' || *r > '9') return 0;
    if (q[0] == '0' && q[1]) return 0;
    errno = 0;
    if (type == 'I' || type == 'S') {
        i = strtoll(p, &end, 10);
        return !errno && !*end && (type == 'S' || (i >= INT32_MIN && i <= INT32_MAX));
    }
    u = strtoull(p, &end, 10);
    if (errno || *end || u > UINT32_MAX) return 0;
    switch (type) {
    case 'B': return u <= 1;
    case 'P': return u >= 1 && u <= 6;
    case 'T': return u >= 1 && u <= 3;
    case 'C': return u == 1 || u == 6;
    case 'N': return u <= 10;
    case 'R': return u <= 6;
    case 'O': return u <= 4;
    case 'L': return u <= 1048576;
    case 'D': return u <= 3;
    case 'K': return u == 0 || u == 2 || u == 7;
    case 'J': return u <= 2;
    case 'F': return u <= 5;
    case 'W': return u == 2 || u == 3;
    case 'Y': return u >= 1 && u <= 6;
    case 'U': return 1;
    case 'Z': return u >= 1 && u <= 2;
    case 'V': return u <= 6;
    case 'E': return u <= 65535;
    case 'G': return u <= 49;
    case 'X': return u <= 5;
    default: return 0;
    }
}

static int match(const char *text, const struct schema *schema)
{
    char input[DIAG_TEXT], fields[DIAG_TEXT];
    size_t prefix = strlen(schema->prefix);
    if (strncmp(text, schema->prefix, prefix) || text[prefix] != ' ') return 0;
    strcpy(input, text + prefix + 1);
    strcpy(fields, schema->fields);
    char *a = input, *b = fields;
    while (a && b) {
        char *an = strchr(a, ' '), *bn = strchr(b, ' ');
        if (an) *an++ = 0;
        if (bn) *bn++ = 0;
        char *av = strchr(a, '='), *bv = strchr(b, '=');
        if (!av || !bv) return 0;
        *av++ = 0; *bv++ = 0;
        if (strcmp(a, b)) return 0;
        if (strlen(bv) == 1 && bv[0] >= 'A' && bv[0] <= 'Z') {
            if (!numeric(av, bv[0])) return 0;
        } else if (strcmp(av, bv)) return 0;
        a = an; b = bn;
    }
    return !a && !b;
}

int diag_parse(const char *record, size_t bytes, char output[DIAG_TEXT])
{
    /* /dev/kmsg: priority,sequence,timestamp,flags;message\n. Do not accept
     * continuation/property lines or embedded NULs, even after a valid prefix. */
    if (bytes >= 8192) return -1;
    if (!bytes || memchr(record, 0, bytes)) return 0;
    const char *semi = memchr(record, ';', bytes);
    if (!semi || semi-record > 96) return 0;
    const char *p = record;
    for (unsigned field = 0; field < 3; ++field) {
        unsigned n = 0;
        while (p < semi && *p >= '0' && *p <= '9') { ++p; ++n; }
        if (!n || n > 20 || p == semi || *p++ != ',') return 0;
    }
    if (p + 1 != semi || (*p != '-' && *p != 'c' && *p != '+')) return 0;
    size_t length = bytes - (size_t)(semi + 1 - record);
    if (length && semi[1 + length - 1] == '\n') --length;
    if (length >= DIAG_TEXT) return -1;
    if (!length) return 0;
    for (size_t j = 0; j < length; ++j)
        if (semi[1+j] < 32 || semi[1+j] > 126) return 0;
    char text[DIAG_TEXT];
    memcpy(text, semi + 1, length); text[length] = 0;
    if (!strcmp(text, "qseecom: Android transport ready (native arm64, whitelist 0)")) {
        strcpy(output, "A9_QSEE_INIT ready=1 whitelist=0"); return 1;
    }
    if (!strcmp(text, "qseecom: Android transport ready (native arm64, whitelist 1)")) {
        strcpy(output, "A9_QSEE_INIT ready=1 whitelist=1"); return 1;
    }
    for (size_t j = 0; j < sizeof(schemas)/sizeof(schemas[0]); ++j) {
        if (match(text, &schemas[j])) { strcpy(output, text); return 1; }
    }
    return 0;
}

void diag_record(struct diag_state *s, const char *record, size_t bytes)
{
    char text[DIAG_TEXT];
    if (bytes >= 8192) { diag_increment(&s->truncated); return; }
    int parsed = diag_parse(record, bytes, text);
    if (parsed < 0) { diag_increment(&s->truncated); return; }
    if (!parsed) return;
    if (s->count == DIAG_QUEUE) { diag_increment(&s->dropped); return; }
    strcpy(s->records[(s->head + s->count) % DIAG_QUEUE], text);
    ++s->count;
}

void diag_flush(struct diag_state *s, diag_submit submit, void *ctx)
{
    while (s->count) {
        if (submit(s->records[s->head], ctx) <= 0) return;
        s->head = (s->head + 1) % DIAG_QUEUE;
        --s->count;
        diag_increment(&s->sent);
    }
}
