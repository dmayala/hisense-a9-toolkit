/* Pinned CF donor compatibility: no Binder/service/PCM execution here.
 * REA Module::populateConnectedDevicePort ELF0x72320 and the backend-pair
 * store in StreamPrimary::setConnectedDevices ELF0xc7678. Object offsets are specific to SHA2fb5385e; build.py refuses
 * another donor. A full source HAL should implement the same two overrides.
 */
typedef unsigned int u32;
typedef unsigned long usize;
typedef unsigned char u8;
#define U32(p,n) (*(const u32 *)((const u8 *)(p)+(n)))
#define PTR(p,n) (*(const u8 *const *)((const u8 *)(p)+(n)))
#define WORD(p,n) (*(const usize *)((const u8 *)(p)+(n)))
static __attribute__((always_inline)) inline usize connection_size(const void *d) {
    u8 tag = *((const u8 *)d+8);
    return tag & 1 ? WORD(d,16) : tag >> 1;
}
static __attribute__((always_inline)) inline int wired_output(const void *d) {
    const u8 *s = (const u8 *)d;
    if (U32(d,0) != 136 && U32(d,0) != 137) return 0;
    if (connection_size(d) != 6) return 0;
    s = s[8] & 1 ? PTR(d,24) : s+9;
    return s[0]=='a' && s[1]=='n' && s[2]=='a' && s[3]=='l' && s[4]=='o' && s[5]=='g';
}
/* Keep built-in devices and the original exception codes. Only static
 * analog headphone/headset OUTPUT profiles are added. No invented mic route. */
__attribute__((section(".text.port_predicate"),noinline))
int a9_port_status(const void *module, const void *port) {
    (void)module;
    if (U32(port,0xe8) != 1) return -3;
    if (!connection_size((const u8 *)port+0x70)) return 0;
    if (wired_output((const u8 *)port+0x70) && WORD(port,0x28)>WORD(port,0x20)) return 0;
    return -5;
}
