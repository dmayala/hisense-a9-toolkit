# A9 Android QSEECom transport

INTEG15 adds the stock QSEE text ring at
`/sys/kernel/debug/tzdbg/qsee_log` (root read-only, nonblocking, per-open cursor).
The private ramdisk logger mounts its own read-only debugfs view and saves
`boot-*/qsee_log` plus rotation on kdebuginfo. Registration/capture failures are
numeric `A9_QSEE_LOG` / `A9_QSEE_CAPTURE` records; an empty file alone is not a
successful TA capture. Raw text stays in private files, never console summaries.
The ABI follows Qualcomm's [stock tz_log.c](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-redbull-4.19-android11-qpr3/drivers/firmware/qcom/tz_log.c):
32 KiB coherent shared bridge, owner50/service1/cmd6 with arginfo0x22, and a
little-endian u16 wrap/u16 offset header. Secure ownership is retained for the
boot even on an ambiguous registration failure; no retry, producer reset or
raw-memory interface is provided. This RAM log registration issues no RPMB
programming operation. The UFS read-only guard remains independent and mandatory.

B-try retains the newest two mainline boot directories so acceptance's B-to-B
restart does not replace the first boot's evidence. Each directory gets separate
numeric RPMB/FBE summaries and private `qseecomd-logcat.log`; the log-root copies
describe the latest boot. Full daemon text remains in `logcat.log[.prev]`.

Built on Stage2 `570e5a2637f3406b9bbc1bb5bc7b271719583352`, including Stage3.
This adds the native ARM64 Android QSEECom char-device ABI to mainline's SCM
implementation. It is host-built experimental kernel code; runtime HAL, TA load,
and real listener callback qualification belong to the coordinator.

## Apply and build

Run `apply.sh PRIVATE_STAGE2_KERNEL_TREE`, enable `qseecom.config`, then use the
Stage2 build recipe. The new frontend is built in with
`CONFIG_QCOM_QSEECOM_ANDROID=y`. It creates `/dev/qseecom` and `/dev/ion` only on
`hisense,hlte556n`, after checking QSEE feature availability. All existing kernel
interfaces and the Stage2/Stage3 crypto paths are retained. No new `.ko` is needed.
The SCM hook is private, unexported, and preserves the downstream result/type/data
triplet while using upstream SCM convention, extended arguments and call locking.

The frontend serializes secure transactions through their listener continuations.
Receive/reply operations wake the originating transaction without taking its
mutex. Listener buffers, modified-command buffers and reply buffers remain
referenced until secure world consumes them. Failed listener deregistration
quarantines its allocation instead of returning potentially live pages to Linux.
There is no callback denial substitute: requests go to the registered userspace
listener and its actual reply goes back to TZ. Listener registration follows
the shipped SM6115 kernel: owner50/service2/command6, arginfo0x83, falling back
to command1 only on transport `-EIO` (unsupported). INTEG15 forced command1,
which returned transport `-EINVAL` before listener publication. The newer ABI
continues a blocked request with command7 and the returned session ID, with
stock's one command4/app-ID fallback on transport failure. Ordinary listener
responses still use command3/5. Transactions remain serialized; this adds no
smcinvoke userspace interface, concurrent secure calls or RPMB write support.

## Implemented userspace ABI

| Operation | Native ioctl |
| --- | --- |
| Listener register / unregister | `c0189701` / `9702` |
| Send / modified send / 64-bit modified send | `c0209703` / `c0409704` / `c0409723` |
| Receive / reply | `9705` / `9706` |
| Modified reply / 64-bit modified reply | `c0309715` / `c0309724` |
| Load / unload app | `c0549707` / `9709` |
| Set shared buffer / get Android ABI version | `c0189708` / `c004970a` |
| Query loaded app | `c048970f`, returns `-EEXIST` with app ID when found |
| Load / unload external ELF | `c054970d` / `970e` |
| Advisory performance enable / disable / bus scale | `970b` / `970c` / `c0049717` |
| Obsolete FBE clear-key | `c048972c`, stock copy-and-return behavior |
| ION direct dma-buf allocation / heap query | `c0184900` / `c0184908` |
| ION legacy free probe | `c0044901`, `-ENOTTY` selects stock libion's direct ABI |
| dma-buf CPU access | `40086200`, DMA core dispatches coherent access hooks |

The two QSEE heap IDs are 19 and 27. Allocations use the DMA API, contiguous
32-bit DMA addresses, coherent kernel/userspace mappings, and normal dma-buf
lifetime/attachment/mmap handling. These provide the access semantics of the
observed QSEE allocations; this is not a general replacement for all ION heaps.
Foreign dma-bufs/scatterlists and secure heaps are not treated as contiguous
QSEE memory. Native ARM64 is the traced stock ABI; 32-bit QSEECom compat is not
implemented. The pointer-free ION ABI supports compat through `compat_ptr_ioctl`.

`libQSEEComAPI.so` also exports FDE/service APIs (ioctls 16/17/18/24). They have
no imports in the traced Keymaster/listener closure and are left unimplemented,
returning `ENOTTY`. This transport neither provisions RPMB nor performs FDE key
management. Performance ioctls validate the request but provide no bandwidth or
clock-performance promise. Functional TA commands and listener replies always
use SCM, with errors returned to userspace.

## Stock evidence

Reference source (GPL-2.0-only): Qualcomm kernel/msm
`android-msm-redbull-4.19-android11-qpr3`, `drivers/misc/qseecom.c`,
`include/uapi/linux/qseecom.h`, `include/soc/qcom/qseecomi.h` and
`drivers/staging/android/uapi/ion.h`:
<https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-redbull-4.19-android11-qpr3/>.
The two UAPI headers in `driver/` are unchanged copies. Driver protocol and
whitelist table formats follow that source; the implementation targets mainline
APIs instead of importing downstream bus, clock, ION and crypto subsystems.

REA/Ghidra evidence is retained under
`/home/user/A9-build/mainline/qseecom-transport-20261021/`. Vendor analysis
addresses below are ELF VAs plus `0x100000`; kernel addresses are link-time VAs.
They describe static code, not observed execution. Firmware-package kernel and
cached stock vendor have distinct provenance; their matching platform does not
assert that they are the current daily-phone bytes.

- Stock `libQSEEComAPI.so`, SHA256 `2f70ad2dd4d3137f18df34022021bc3bf9533ac2161de0e1afc6e7d338d82094`:
  get-handle `105014`, query `1054b8`, image load `10561c`, ION allocation
  `105d24`, listener register `1064b0`, send `1068dc`, modified send `106b30`,
  receive `106dfc`, reply `106f54`, modified reply `107c38`. Complete 28 ioctl
  instruction sites are in `client-ioctl-sites.dis`; `rea-client-full.json`
  retains 14 function dossiers. The byte fixtures use the observed stores,
  including modified-send FD array offset 28 and modified-reply offset 12.
- Stock qseecomd, SHA256 `5be26351f46f1e129fa5c780bbb49817bd91f38d58cadc07152620ccc4fc1f51`:
  main `102334`, listener dispatcher `10206c`. It dynamically starts ten listener
  entries. The complete import closure is in `vendor-transport-imports.json`;
  the eight listener objects use register/receive/reply/unregister, with
  secure-UI also using both modified-reply widths. RPMB's direct block ioctls
  are a userspace/device integration requirement, not QSEECom transport calls.
- Packaged stock system/VNDK libion, SHA256
  `113154f43bb5ecedd5d627032976f15ba8acb80183cf1b053f49778bddb15726`:
  `rea-ion.json` records `ion_alloc_fd`, `ion_is_legacy` and both heap-query
  APIs. `ION_IOC_FREE(0)` must return `ENOTTY`; otherwise this library chooses
  obsolete allocate/share/free handles. `extract-stock-libion.py` reads only
  selected files through package rawprogram/LP/ext4 mappings, with no full
  image extraction or device access.
- Stock Keymaster direct call at ELF `16cbc` (analysis `116cbc`) uses
  `c048972c` in legacy deletion. `rea-keymaster-direct.json` and
  `stock-ioctl44-pseudo.txt` retain it. Package kernel SHA256
  `0fd46f80770193f08633e9c5d31a6137800be0436524db743bd7f66db8154b01`:
  compare/branch `ffffff80087aeffc..87af008`; copy72 and zero return
  `ffffff80087b01e0..87b0248`. There is no SCM or keyslot operation on this
  branch. No claim that this ioctl clears a blk-crypto key is made.
- Stock SCM dispatch `ffffff80087b5848` confirms OS owner50, app owner48,
  app-manager service1 and listener service2. Carrier-only decompilation has
  unmapped external data/callee limitations; original-address disassembly and
  the GPL source supply the complete protocol. The stock DT says ABL loads
  both common libraries. The inherited A9 DT already reserves
  `60000000..65c00000`, covering the stock secure-app region.

## Integration for agent

Use the new boot and matching vendor from the output report. The 100 Stage2
modules are unchanged; vendor packaging changes only their kernel-image hash
manifest. Merge `ueventd.qseecom.rc` into the active ueventd file and supply
`tee_device`/`ion_device` labels and the actual ioctl permissions in the existing
enforcing service domains. The driver initially creates both devices mode0600;
ueventd must set permissions before qseecomd drops privileges to system.

Keep stock qseecomd and Keymaster service ordering, libraries, listener workers
and block access as stock does. This source does not install/enable services,
select a Keymaster provider, format storage, provision secure state, or provide a
ready wrapped-FBE activation image. The coordinator-supplied R3 diagnostic
establishes preloaded keymaster64 lookup/GET_VERSION through upstream SCM;
it does not test this frontend or real listener replies.

Run `tests/run.sh OUTPUT_DIRECTORY` with the Stage2 host/cross toolchain PATH.
It executes native ABI byte fixtures and the actual production buffer-boundary
helpers, and independently compiles both for ARM64. Final build, 100-module ABI,
image readback, boot/recovery ABL checks and archive receipts are in the output
report. No phone access or independent runtime review occurs in this task.

## FP4 resource-vote correction

`0002-a9-qseecom-resource-votes.patch` restores the stock CE1/crypto-bus votes
around Android OS/app SCM calls. The SM6115 board must also include
`a9-qseecom-clock.dtsi` (or the identical inline Android-tree block) so the SCM
node supplies its separate `qsee` clock at 192 MHz. The fingerprint FP4 builder
applies that DT overlay and verifies it. See `../fingerprint/README.md`, FP4,
for the saved LOAD_APP failure, stock REA addresses, limitations and native
module-ABI evidence. The frontend's new LOAD diagnostics distinguish request
validation, imported-buffer bounds, raw SCM and listener completion failures.

## FP6 APP_START range correction

Stock TZ validates `mdt_len + img_len`, while the shipped library already
includes MDT in `img_len`. A whole-heap stock SHM registration covered that
additional range; FP5's per-allocation registration did not. LOAD now stages
the unchanged bytes in a bounded zero-filled contiguous allocation registered
for the complete sum, retaining it through listener completion. See the FP6
section of `../fingerprint/README.md` for shipped TZ/kernel/library addresses,
REA records, exact sizes and limitations. The UAPI, SCM descriptor, TA files,
secure destination reservation and ABL region handoff are preserved.
