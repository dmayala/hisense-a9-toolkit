// SPDX-License-Identifier: Apache-2.0
// HCI packet bodies exclude the one-byte H4 type used by HCI_CHANNEL_USER.
#pragma once
#include <cstddef>
#include <cstdint>
inline bool valid_hci_packet(uint8_t type, const uint8_t* p, size_t n) {
    switch (type) {
    case 1: return n >= 3 && n == size_t(3 + p[2]);
    case 2: return n >= 4 && n == size_t(4 + p[2] + (unsigned(p[3]) << 8));
    case 3: return n >= 3 && n == size_t(3 + p[2]);
    case 4: return n >= 2 && n == size_t(2 + p[1]);
    case 5: return n >= 4 && n == size_t(4 + ((p[2] | (unsigned(p[3]) << 8)) & 0x3fff));
    default: return false;
    }
}
