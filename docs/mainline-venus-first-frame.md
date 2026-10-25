# Venus first filled input: boot132

Follow-up: [integ28 manual encoder reset](mainline-venus-integ28-reset.md) explains
the operation counters, stock recovery addresses, previous-boot log pull and
isolated Y-only comparison on the already shipped module.

2026-10-25, host-only. **Hardware encode remains unqualified.** The strongest
first-buffer hypothesis is a DMA access/ownership fault. A concrete stock parity
experiment now makes encoder mappings bidirectional; no retained SMMU syndrome,
fault address or firmware exception proves that this caused the reset. A separate,
confirmed recovery bug is fixed: the shipped driver saved the Venus watchdog IRQ
status but never dispatched its existing watchdog event handler.

## What the failed boot establishes

The private coordinator capture is
`/home/user/A9-build/mainline/sweep-integ27r-f1-20261025/boot-0000000132/kernel.log`.
LOAD_RES and START both responded with rc0. State5 then accepted FTB type2,
allocation2097152, and ETB type1, allocation147456, **filled147456**, offset0,
flags0, tag0. Each host submission returned0; there is no EBD/FBD afterward.
The retained kernel tail ends at about489.58s. The coordinator observed USB vanish
and the phone restart. Queue acceptance establishes neither firmware execution
nor completion. There is no retained SMMU/NoC/panic/watchdog report identifying
the reset source. Zero previous-pstore records does not establish a clean reset.

`a9_output_size` and `a9_stock_work_mode` were already enabled, with successful
property submissions. EOS was never reached, so the `0xdeadb000` EOS discrepancy
cannot explain this particular observed boundary. CPU-readable input got beyond
the earlier userspace mapping failure; it does not prove every cache transition.

## Stock comparison at first ETB

Stock addresses are unslid kernel VAs in the saved 4.19.157 Image, SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
REA used the address-preserving relocation carrier from the
[HFI comparison](mainline-venus-hfi-compare.md), SHA256
`040492d327d6737b1d959fe39fb0f3c1a03d2c88e8c0050d9c9f1fc4a8eb17c0`.
Its code bytes are unchanged; synthetic symbol boundaries and decompiler warnings
limit interpretation. The Xtensa firmware was not decompiled or executed.

| Item | Stock versus mainline | Interpretation |
| --- | --- | --- |
| Raw format and constraints | Stock raw-format setter `0xffffff8008dfee78` supports NV12 plus constraints `0x201002`; its initial Q128 UBWC default is not a matched NV12 session. Mainline negotiates linear NV12 and sends actual plane/stride property `0x1002`. Full downstream constraint-enum conversion remains unresolved. | Possible remaining property difference; no evidence the submitted frame was RGBA/UBWC mislabeled as NV12. |
| Geometry and UV | Mainline's 320x240 request uses padded384x256, stride384, Y98304, UV49152, total147456 (`0x24000`). UV starts at base+data_offset+98304 in the same V4L2 memory plane. V4L2Device derives the same offset from negotiated bytesperline*height. | Exactly matches firmware input requirement147456. No missing second UV registration or demonstrated undersize. Actual pixel contents were not retained. |
| Extradata and ETB | Stock `populate_frame_data@0xffffff8008dd4788` copies mapped address, allocation, filled length and data_offset. Optional second V4L2 plane is extradata, not UV. ETB builder `0xffffff8008e60218` sends these in its64-byte packet. Mainline has the same ordinary ETB field layout. | No observed extradata requirement or offset violation; firmware-internal parsing remains unknown. |
| User allocations/counts | Firmware requested input147456/min3/count3 and output230400/min2/count2. Submitted output2097152 exceeds its requirement. Both drivers send count property `0x201001`. | Logs do not contain actual count payloads; one initial ETB does not establish only one input allocation. Do not infer count mismatch. |
| Scratch/persist | Stock allocator `0xffffff8008e21d28` page-rounds allocation and registration `0xffffff8008dd4a50` uses that length. Mainline registers exact firmware lengths, while DMA backing is page-rounded. Boot132 types7/8/4:382768/295168/16392; rounded:385024/299008/20480. | Concrete isolated follow-up, below DMA permission/ownership in first-access priority. Both cover requested bytes; not proved underallocation. No rounding variant landed here. |
| Nonsecure bank | Stock `msm_smem_get_context_bank@0xffffff8008e23a10` selects venus_ns for all nonsecure encoder types. Saved DT uses SID/mask860/0 and880/0, matching mainline. Secure banks have distinct stream IDs and selection rules. | No support for switching to a secure CB. Static stream IDs do not establish actual live CB/TTBR or fault direction. |
| Mapping permissions | Stock `msm_dma_get_device_address@0xffffff8008e204b8` maps every imported buffer with direction0, DMA_BIDIRECTIONAL; attachment attributes0x10020 or0x12020. Mainline HFI4 input is DMA_TO_DEVICE, capture DMA_FROM_DEVICE. | **Concrete experiment:** stock permits writes to input/readbacks from capture that direction-restricted mappings may reject. No captured fault proves firmware made either access. Downstream attribute bits are not blindly copied. |
| Cache ownership | Stock qbuf helper `0xffffff8008dcee78` calls `msm_smem_cache_operations@0xffffff8008e23478`; cached raw input gets partial begin/end CPU access with directionTO_DEVICE, offset and filled length. Mainline vb2 DMABUF prepare/finish relies on exporter/userspace synchronization. | Real contract difference; CPU-readable allocation alone does not prove a flush. Exact mapper/converter ioctl-sync path still needs checking before changing cache ownership. Bidirectional mapping is not a cache fix. |
| IOVA placement | Stock NS pool is `[0x70800000,0xe0000000)`, with best-fit enabled by DT setup. Mainline mask ends0xdfffffff, protection boundary0x70800000 matches, and generic allocation searches top-down but has no explicit stock lower pool. | Compare actual IOVA intervals before blaming placement. No runtime IOVAs were logged; same SIDs/boundary weaken a blanket wrong-bank claim. |

The stock mapping call names resolve through the saved full-kernel symbol map.
Six new REA records and selected DT properties are retained privately under
`/home/user/A9-build/mainline/venus132-20261025/stock-mapping-rea/` and
`stock-mapping-dt.json`. `stock-mapping.md` indexes all evidence IDs and caveats.
The earlier [causality ranking](mainline-venus-hfi-causality.md) retains other
properties and startup clock/bandwidth differences; they remain candidates,
not demonstrated causes. Highest next priorities are actual DMA access validity
and cache ownership, then registered internal lengths and startup votes.

## Landed behavior and next comparison

`sm6115-first-frame.patch` adds default-off module parameter
`venus_enc.a9_bidir_buffers`. It is sampled per new codec instance. With the flag,
only SM6115 + HFI4 + AR50_LITE makes both vb2 queues bidirectional; default and
other platforms preserve their behavior, including legacy HFI1 input mapping.
Queue setup precedes firmware negotiation, so this flag gates on the device and
HFI version, **not a runtime firmware-version value**. The image pins6.0.39.
Private per-instance storage avoids changes to exported Venus structure layouts.
It does not alter HFI payloads, format, secure banks, clocks or RPMB.

The coordinator can compare otherwise identical fresh encoders with
`/sys/module/venus_enc/parameters/a9_bidir_buffers` set to N versus Y, holding
CPU-readable input and the existing three variant settings fixed. Change the
parameter **before codec creation**. The Y case emits
`A9_VENUS_ENC op=a9_bidir_buffers phase=end rc=0 ...` at queue initialization.
Use the existing `A9_SWEEP_HVENC_QUALIFY` stage; agent owns its userspace.
This worker made no phone attempts or changes to that owner's run scheduling.
Passing requires real encoded/decode-checked output and EOS, not successful QBUF.
A successful Y case would implicate permissions/mapping semantics but would not
by itself prove the exact faulting firmware instruction.

## Recovery and capture

The exact packaged predecessor `venus-core.ko` SHA256 was
`d92525d991a81680a9f6db757a7ddc1aeae7ed1585bb16f5ad253dd7c777bbde`.
Its matching `hfi_venus.c` SHA256 was
`8a3e0f50b0894063e91c4f7040f22f2bd443be45525521eed6e5877cab1df486`;
its object had no reference to `hfi_process_watchdog_timeout`.

The top half latched watchdog status; `venus_isr_thread` ignored it. The patch
consumes/clears that status under existing IRQF_ONESHOT serialization, selects
AR50_LITE mask0x10 (IRIS2 mask0x8), logs
`A9_VENUS_RECOVERY event=1 stage=0 rc=0 irq=...` before taking recovery locks,
marks DEINIT, prints the private SFR, and dispatches the existing watchdog helper.
It returns before normal packet processing so a coincident SYS_ERROR does not
notify twice. Existing core handling fails sessions and schedules Venus recovery
10ms later, then shuts down/reboots/reinitializes firmware. This repair is always
on; the DMA experiment remains off by default.

The marker is written to the existing console-ramoops path and now gets immediate
write-before-fsync treatment in the boot logger. No per-frame extra sync is added.
The shipped kernel does **not** export `kmsg_dump_desc` to modules; the final patch
has no new snapshot import or kernel-export change. No ramoops geometry, watchdog
owner, reset timing or panic policy changes are needed. Existing console ring is
256KiB inside1MiB reserved RAM at0xb0580000; the previous-boot logger copies records
without erasing them. A Venus-local watchdog can now reach existing SSR while
APSS remains responsive. A global CPU/fabric/SCM stall, failed recovery operation
or RAM clearing across reset can still defeat recovery or retention. No runtime
recovery/persistence claim is made.

The host extractor now retains literal `phase=buffer.end` (previously silently
rejected), keeps the fixed numeric watchdog marker, and counts it as a firmware
error, never a completion. Previous pstore remains excluded from current-boot
qualification. Next-run evidence should therefore distinguish queue acceptance,
actual EBD/FBD, and observed firmware watchdog; absence of a watchdog marker
still cannot distinguish a whole-SoC stall from lost capture.

## Host validation

Private build root: `/home/user/A9-build/mainline/venus132-20261025/build`.
`kernel-trace-checks.json` binds all three compiled modules to a fuzz0 replay of
all five patches, identical exported CRCs and the integ27 kernel import CRCs and
vermagic. `stage-encoder-trace.py --trace-build <that root>` requires these new
source bytes and pins the patch in the stage receipt; old stages remain intact.
The full userspace stage must be refreshed by its owner against current sources.

Actual-handler host stubs cover normal IRQ processing, platform masks, single
notification and stale-status consumption; actual queue initializer tests cover
default/target exclusions, HFI1 preservation and queue errors. Existing mapped
EOS and startup packet tests, six qualification fixture groups, numeric replay
of boot132, logger write-before-fsync tests and the production AArch64 static
logger build pass. No firmware, TA, device or secure storage was executed/accessed.
