// SPDX-License-Identifier: Apache-2.0
// Observe the existing stock Keymaster path. Never retry, upgrade or replace keys.
#define _GNU_SOURCE
#include <stddef.h>
#ifdef A9_FBE_HOST_TEST
#define ANDROID_LOG_INFO 4
extern int __android_log_print(int, const char *, const char *, ...);
#else
#include <android/log.h>
#endif
#include <dlfcn.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include "fbe-trace-abi.h"
#include "fbe-public-auth.h"

_Static_assert(sizeof(struct param) == 24, "stock ARM64 parameter ABI");
_Static_assert(sizeof(struct params) == 16, "stock ARM64 set ABI");
_Static_assert(offsetof(struct characteristics, hardware) == 0, "stock hw_enforced first");
_Static_assert(offsetof(struct characteristics, software) == 16, "stock sw_enforced second");
#define APP_ID 0x90000259U
#define APP_DATA 0x900002bcU

typedef int (*chars_fn)(void *, const struct blob *, const struct blob *,
                        const struct blob *, struct characteristics *);

static void release(struct params *p) {
    for (size_t i = 0; i < p->size; ++i) {
        unsigned type = p->data[i].tag >> 28;
        if (type == 9 || type == 10) free((void *)p->data[i].value.blob.data);
    }
    free(p->data);
}

int traced_begin(void *, uint32_t, const struct blob *, const struct params *,
                 struct params *, uint64_t *) __asm__(BEGIN);
int traced_begin(void *device, uint32_t purpose, const struct blob *key,
                 const struct params *input, struct params *output, uint64_t *handle) {
    begin_fn original = (begin_fn)dlsym(RTLD_NEXT, BEGIN);
    if (!original) return -1000;
    // Preserve the original result, parameters, handle and number of attempts.
    int rc = original(device, purpose, key, input, output, handle);
    struct timespec now;
    static unsigned reported;
    if (rc >= 0 || purpose != 1 || !input || input->size > 64 ||
        (input->size && !input->data) ||
        clock_gettime(CLOCK_BOOTTIME, &now) || now.tv_sec > 120 ||
        __atomic_fetch_add(&reported, 1, __ATOMIC_RELAXED) >= 2) return rc;
    struct blob app_id = {0}, app_data = {0};
    unsigned requested_gcm = 0;
    for (size_t i = 0; i < input->size; ++i) {
        const struct param *p = &input->data[i];
        if (p->tag == APP_ID) app_id = p->value.blob;
        if (p->tag == APP_DATA) app_data = p->value.blob;
        if (p->tag == 0x20000004U && p->value.integer == 32) requested_gcm = 1;
    }
    struct characteristics c = {0};
    chars_fn get = (chars_fn)dlsym(RTLD_NEXT, CHARS);
    int cr = get ? get(device, key, &app_id, &app_data, &c) : -1000;
    unsigned aes = 0, gcm = 0, decrypt = 0, os_version = 0, os_patch = 0, vendor_patch = 0;
    if (!cr) {
        for (size_t i = 0; i < c.hardware.size; ++i) {
            const struct param *p = &c.hardware.data[i];
            unsigned v = p->value.integer;
            if (p->tag == 0x10000002U && v == 32) aes = 1;
            if (p->tag == 0x20000004U && v == 32) gcm = 1;
            if (p->tag == 0x20000001U && v == 1) decrypt = 1;
            if (p->tag == 0x300002c1U) os_version = v;
            if (p->tag == 0x300002c2U) os_patch = v;
            if (p->tag == 0x300002ceU) vendor_patch = v;
        }
    }
    // Fixed public fields only: no bytes, hashes, names, IDs, HAT, nonce or appId.
    __android_log_print(ANDROID_LOG_INFO, "A9FBE",
        "A9_FBE_KEY begin_rc=%d chars_rc=%d requested_gcm=%u hardware_aes=%u "
        "hardware_gcm=%u hardware_decrypt=%u os_version=%u os_patch=%u vendor_patch=%u",
        rc, cr, requested_gcm, aes, gcm, decrypt, os_version, os_patch, vendor_patch);
    // The TA can reject characteristics before its client parses the clear
    // KMKD authorization map. Inspect that public map without another TA call,
    // and label it unverified; it never changes a key or operation parameter.
    struct fbe_auth local;
    int lr = key ? fbe_public_auth(key->data, key->size, &local) : -33;
    if (!key) memset(&local, 0, sizeof(local));
    __android_log_print(ANDROID_LOG_INFO, "A9FBE",
        "A9_FBE_LOCAL local_rc=%d verified=0 hardware_aes=%u hardware_gcm=%u "
        "hardware_decrypt=%u os_version=%u os_patch=%u vendor_patch=%u boot_patch_present=%u boot_patch=%u",
        lr, local.aes, local.gcm, local.decrypt, local.os, local.patch, local.vendor,
        local.boot_present, local.boot);
    release(&c.software);
    release(&c.hardware);
    return rc;
}

// Observe the stock HAL's existing configure transaction. Do not manufacture
// boot parameters or change the real system/vendor versions passed to the TA.
int traced_configure(void *, const struct params *) __asm__(CONFIGURE);
int traced_configure(void *device, const struct params *input) {
    configure_fn original = (configure_fn)dlsym(RTLD_NEXT, CONFIGURE);
    if (!original) return -1000;
    int rc = original(device, input);
    static unsigned reported;
    if (!input || input->size > 64 || (input->size && !input->data) ||
        __atomic_fetch_add(&reported, 1, __ATOMIC_RELAXED) >= 2) return rc;
    unsigned os = 0, patch = 0, vendor = 0, boot = 0, present = 0;
    for (size_t i = 0; i < input->size; ++i) {
        const struct param *p = &input->data[i];
        if (p->tag == 0x300002c1U) os = p->value.integer;
        if (p->tag == 0x300002c2U) patch = p->value.integer;
        if (p->tag == 0x300002ceU) vendor = p->value.integer;
        if (p->tag == 0x300002cfU) { boot = p->value.integer; present = 1; }
    }
    __android_log_print(ANDROID_LOG_INFO, "A9FBE",
        "A9_FBE_CONFIG configure_rc=%d os_version=%u os_patch=%u vendor_patch=%u "
        "boot_patch_present=%u boot_patch=%u", rc, os, patch, vendor, present, boot);
    return rc;
}
