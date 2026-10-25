// SPDX-License-Identifier: Apache-2.0
#include <stddef.h>
#include <stdint.h>
struct blob { const uint8_t *data; size_t size; };
struct param {
    uint32_t tag;
    union { uint32_t integer; uint64_t wide; struct blob blob; } value;
};
struct params { struct param *data; size_t size; };
// Stock keymaster_defs.h: hw_enforced precedes sw_enforced.
struct characteristics { struct params hardware, software; };
#define BEGIN "_ZN15keymasterdevice18KeymasterHalDevice15begin_operationE19keymaster_purpose_tPK20keymaster_key_blob_tPK25keymaster_key_param_set_tPS5_Pm"
#define CHARS "_ZN15keymasterdevice18KeymasterHalDevice23get_key_characteristicsEPK20keymaster_key_blob_tPK16keymaster_blob_tS6_P31keymaster_key_characteristics_t"
typedef int (*begin_fn)(void *, uint32_t, const struct blob *, const struct params *,
                        struct params *, uint64_t *);

#define CONFIGURE "_ZN15keymasterdevice18KeymasterHalDevice9configureEPK25keymaster_key_param_set_t"
typedef int (*configure_fn)(void *, const struct params *);
