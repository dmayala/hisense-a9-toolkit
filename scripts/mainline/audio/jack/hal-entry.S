/* C++ ScopedAStatus returns through x8, unlike a trivial C pointer wrapper. */
.section .text.port_entry,"ax",%progbits
.global a9_connected_port
a9_connected_port:
    stp x19, x30, [sp, #-16]!
    mov x19, x8
    bl a9_port_status
    cbnz w0, 1f
    bl AStatus_newOk
    b 2f
1:  bl AStatus_fromExceptionCode
2:  str x0, [x19]
    ldp x19, x30, [sp], #16
    ret

/* StreamPrimary::setConnectedDevices ELF0xc7678 originally stores x23.
 * x20=devices, x21=this, x22=held mutex, x23=parsed card/device pair.
 * Resolve an address-less analog OUTPUT to existing PCM25 WCD route.
 * Preserve all original flow, input, ignored output and non-wired aliases.
 */
.section .text.wired_select,"ax",%progbits
.global a9_wired_select
a9_wired_select:
    cmn x23, #1
    b.eq 3f
    ldrb w8, [x21, #0x84]
    cbnz w8, 3f
    ldp x8, x9, [x20]
    sub x9, x9, x8
    cmp x9, #0x40
    b.ne 3f
    ldr w9, [x8]
    cmp w9, #136
    b.eq 1f
    cmp w9, #137
    b.ne 3f
1:  ldrb w9, [x8, #8]
    tbnz w9, #0, 2f
    lsr x10, x9, #1
    add x11, x8, #9
    b 4f
2:  ldp x10, x11, [x8, #16]
4:  cmp x10, #6
    b.ne 3f
    ldr w9, [x11]
    mov w10, #0x6e61
    movk w10, #0x6c61, lsl #16
    cmp w9, w10
    b.ne 3f
    ldrh w9, [x11, #4]
    mov w10, #0x676f
    cmp w9, w10
    b.ne 3f
    mov x23, #0x1900000000
3:  str x23, [x21, #0x1c0]
    b a9_store_continue
