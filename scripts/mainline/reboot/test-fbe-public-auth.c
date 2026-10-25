// SPDX-License-Identifier: Apache-2.0
// Synthetic fixtures only, supplied by test-fbe-public-auth.py.
#include <stdio.h>
#include "fbe-public-auth.h"
int main(void) {
    uint8_t bytes[65537];
    size_t size = fread(bytes, 1, sizeof(bytes), stdin);
    struct fbe_auth auth;
    int rc = fbe_public_auth(bytes, size, &auth);
    printf("%d %u %u %u %u %u %u %u %u\n", rc, auth.aes, auth.gcm,
           auth.decrypt, auth.os, auth.patch, auth.vendor, auth.boot_present, auth.boot);
    return 0;
}
