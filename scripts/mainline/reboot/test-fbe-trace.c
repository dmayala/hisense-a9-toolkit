// SPDX-License-Identifier: Apache-2.0
// Fake stock backend: no hardware, files, Binder, QSEE, keys or credentials.
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "fbe-trace-abi.h"

#ifdef BACKEND
int original_configure(void *, const struct params *) __asm__(CONFIGURE);
int original_configure(void *dev, const struct params *in) {
    assert(dev == (void *)0x1234 && in->size == 3);
    assert(in->data[0].value.integer == 160000 && in->data[1].value.integer == 202605 && in->data[2].value.integer == 20220405);
    puts("BACKEND configure_called=1");
    return atoi(getenv("CHARS_RC"));
}
int original_begin(void *, uint32_t, const struct blob *, const struct params *,
                   struct params *, uint64_t *) __asm__(BEGIN);
int original_begin(void *dev, uint32_t purpose, const struct blob *blob,
                   const struct params *params, struct params *out, uint64_t *handle) {
    assert(dev == (void *)0x1234 && purpose == 1 && blob && params && out && handle);
    assert(blob->size == 16 && !memcmp(blob->data, "SYNTHETIC_SECRET", 16));
    assert(params->size == 3 && params->data[0].value.integer == 32);
    assert(params->data[1].value.blob.size == 16);
    puts("BACKEND begin_called=1");
    return atoi(getenv("BEGIN_RC"));
}
int characteristics(void *, const struct blob *, const struct blob *,
                    const struct blob *, struct characteristics *) __asm__(CHARS);
int characteristics(void *dev, const struct blob *key, const struct blob *id,
                    const struct blob *data, struct characteristics *out) {
    assert(dev == (void *)0x1234 && key->size == 16 && id->size == 16 && data->size == 0);
    assert(!memcmp(id->data, "SYNTHETIC_SECRET", 16));
    puts("BACKEND characteristics_called=1");
    int rc = atoi(getenv("CHARS_RC"));
    if (rc) return rc;
    const uint32_t tags[] = {0x10000002, 0x20000004, 0x20000001,
                              0x300002c1, 0x300002c2, 0x300002ce, 0xa00001f6};
    const uint32_t values[] = {32, 32, 1, 160000, 202605, 20220405, 0};
    // Independently write the published stock ABI offsets. This must not use
    // the shim's field names, which previously mirrored a reversed layout.
    struct params *hw = (struct params *)((unsigned char *)out + 0);
    struct params *sw = (struct params *)((unsigned char *)out + 16);
    hw->data = calloc(7, sizeof(struct param)); hw->size = 7;
    sw->data = calloc(1, sizeof(struct param)); sw->size = 1;
    // Software-only algorithm differs deliberately: reversed ABI would fail.
    sw->data[0].tag = 0x10000002; sw->data[0].value.integer = 128;
    for (size_t i = 0; i < 7; ++i) {
        hw->data[i].tag = tags[i];
        hw->data[i].value.integer = values[i];
    }
    hw->data[6].value.blob.data = (uint8_t *)strdup("SYNTHETIC_SECRET");
    hw->data[6].value.blob.size = 16;
    return 0;
}
#else
int clock_gettime(clockid_t id, struct timespec *out) {
    if (id != CLOCK_BOOTTIME) return (int)syscall(SYS_clock_gettime, id, out);
    out->tv_sec = 20; out->tv_nsec = 0; return 0;
}
int __android_log_print(int priority, const char *tag, const char *fmt, ...) {
    assert(priority == 4 && !strcmp(tag, "A9FBE"));
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); puts(""); return 0;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    void *shim = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    assert(shim);
    void *backend = dlopen("libfake-stock.so", RTLD_NOW | RTLD_GLOBAL);
    assert(backend);
    configure_fn configure = (configure_fn)dlsym(shim, CONFIGURE); assert(configure);
    struct param cp[3] = {{.tag=0x300002c1,.value.integer=160000},
                         {.tag=0x300002c2,.value.integer=202605},
                         {.tag=0x300002ce,.value.integer=20220405}};
    struct params config = {cp, 3};
    assert(configure((void *)0x1234, &config) == atoi(getenv("CHARS_RC")));
    assert(config.data == cp && config.size == 3 && cp[2].value.integer == 20220405);
    begin_fn begin = (begin_fn)dlsym(shim, BEGIN); assert(begin);
    const char *secret = "SYNTHETIC_SECRET";
    struct blob key = {(const uint8_t *)secret, 16};
    struct param p[3] = {{.tag = 0x20000004, .value.integer = 32},
                         {.tag = 0x90000259, .value.blob = {(const uint8_t *)secret, 16}},
                         {.tag = 0x900002bc, .value.blob = {NULL, 0}}};
    struct params in = {p, 3}, out = {(void *)0x5678, 7};
    uint64_t handle = 0xabcdef;
    for (int i = 0; i < 3; ++i) {
        int rc = begin((void *)0x1234, 1, &key, &in, &out, &handle);
        assert(rc == atoi(getenv("BEGIN_RC")));
        assert(out.data == (void *)0x5678 && out.size == 7 && handle == 0xabcdef);
        assert(in.data == p && in.size == 3 && p[0].value.integer == 32);
    }
    puts("PASS original_result_and_parameters_preserved=1");
    return 0;
}
#endif
