# A9 RAM-only rmtfs source audit and patch

2026-10-16. Status: implemented draft with offline host checks. No phone access or modem start was performed. No runtime firmware-tolerance or whole-SoC flash-isolation proof is claimed.

This audit supersedes the userspace-source gap in the earlier offline Wi-Fi plan. The source snapshot is now available locally, read-only, at `~/A9-build/mainline/sol-wifi/src/rmtfs`, commit **b30a3eb38f9af283f18dbd3c7755653efc52c094**. The linked QRTR source is **29e36ae164389580a0f8ea7a7fdb728140ae978d**. The patch is `scripts/mainline/wifi/patches/rmtfs.patch`; it is applied only to a copied build tree. The original snapshot is never changed.

## What the upstream source actually does

All upstream references here use the pinned unpatched source line numbers.

| Source | Complete filesystem/control surface |
| --- | --- |
| `storage.c` | The only EFS backing-file opens/writes; detailed below. |
| `sharedmem.c` | RFSA character device, UIO, sysfs/udev discovery, reserved-memory DT traversal and `/dev/mem` fallback; detailed below. |
| `rproc.c` | Enumerates remoteprocs, opens a matching `state` writable, and issues start/stop through worker threads. |
| `rmtfs.c` | QRTR/QMI dispatch, signal handling, optional rproc lifecycle and reconnect loop; no direct EFS path opens. |
| `qmi_rmtfs.c` | Generated QMI field descriptions only; no filesystem operations. |
| `qmi_tlv.c` | Older standalone TLV allocator/parser; no filesystem operations, and not included by either upstream Makefile or Android.bp. It remains excluded. |
| `util.c` | Hex formatting to stdio only; no path operations. |
| `rmtfs.h`, `qmi_rmtfs.h`, `util.h` | Declarations, structures and constants; no hidden filesystem implementations. |
| `Makefile`, `Android.bp`, service/rule files | Upstream build includes rproc/sharedmem/storage. The systemd unit invokes `-r -P -s` and restarts automatically; the directory unit invokes `-s -o ...`. Neither unit is installed by the A9 path. |

`storage.c:39` maps exact modem protocol names to backing basenames and partition labels. This includes `/boot/modem_fs1` → `modem_fs1`/`modemst1`, `/boot/modem_fs2` → `modem_fs2`/`modemst2`, `/boot/modem_fsc` → `modem_fsc`/`fsc`, `/boot/modem_fsg` → `modem_fsg`/`fsg`, plus study/tuning and OnePlus/Oppo names. Unknown names are rejected, but known names may resolve to arbitrary host files through the configured directory and symlinks. There is no regular-file, filesystem, ownership or size check.

The flags are established by `rmtfs.c:508`–`:560` and `storage.c:59`–`:110`:

| Flag | Actual behavior |
| --- | --- |
| none | Uses `/boot/<actual basename>`, opened O_RDWR. |
| `-o DIR` | Changes the backing directory. It does not itself force RAM, regular files or read-only operation. |
| `-P` | Uses partition-label basenames instead of image basenames. Without `-o`, the directory becomes `/dev/disk/by-partlabel`; Android commonly overrides it to `/dev/block/by-name`. |
| `-r` | Opens the selected original O_RDONLY, reads it into a heap shadow, then closes the original. Subsequent protocol writes **succeed into the shadow**, and sync is a no-op. This is copy-on-write behavior, not protocol write rejection. |
| `-S X` | On initial open failure, retries the same basename with `_X` appended. It is a fallback lookup, not partition isolation. |
| `-s` | Calls rproc_init, opens matching remoteproc state writable, then starts the modem after publishing RMTFS and stops it on the first termination signal. It is lifecycle synchronization, not fdatasync configuration. |
| `-v` | Enables debug printing. |

`storage.c:148`–`:167` joins the directory and the selected basename, then retries with the slot suffix. `:90` opens O_RDWR without O_NOFOLLOW. `:288` opens O_RDONLY for shadow mode. `:242` sends writes directly to the open FD in ordinary mode; `:272` calls fdatasync. In shadow mode, writes alter/reallocate the heap up to the nominal 16 MiB cap, and `storage_close:176` frees that shadow. Closing and reopening therefore discards shadow changes even within the same daemon session.

Important upstream error/validation findings: `storage_get:190` does not reject negative caller IDs or inactive handles; `storage_pread:222` can use a negative pread result in its zero-fill pointer/size; shadow writes do not robustly validate negative offsets/overflow. In `rmtfs.c`, a failed write-handle lookup can reach storage_sync(NULL), sync failures are ignored, GET_DEV_ERROR has an inverted NULL check, and sector multiplication uses 32-bit arithmetic before conversion. The A9 path replaces the backing implementation and corrects these dispatch issues.

`sharedmem.c:54`–`:202` tries `/dev/qcom_rmtfs_mem1` and obtains physical address/size through udev; its Android branch `:205` reads `/sys/class/rmtfs/qcom_rmtfs_mem1/{phys_addr,size}` directly. `rmtfs_mem_open:268` falls back on ENOENT to `/dev/qcom_rmtfs_uio1`, then reserved-memory DT traversal and **`open("/dev/mem", O_RDWR|O_SYNC)`** at `:296`. The DT fallback opens `/proc/device-tree/reserved-memory/`, matching child directories and their `reg` files (`:417`, `:431`, `:439`). Mapped transfers check the region through `rmtfs_mem_ptr:338`; the direct character-device pread/pwrite branches at `:368`/`:387` lack the equivalent explicit arithmetic checks in this userspace source. No ioctl is used. All this legacy implementation is excluded from the A9 build.

`rproc.c:34`–`:78` walks `/sys/class/remoteproc`, reads `device/modalias`, accepts MPSS/MSS matches and opens `state` O_WRONLY. Its fallback at `:94` walks the `qcom-q6v5-mss` driver tree. `:179`/`:202` pwrite `start`/`stop`. The source does not verify the A9 platform address. `rmtfs.c:449` starts it when `-s` supplied an FD; `:581` automatically reconnects after ENETRESET. A9 excludes rproc.c entirely and removes those calls and that retry loop.

The linked QRTR library was also checked: `lib/qrtr.c` uses sockets/bind/getsockname/sendto rather than EFS paths; its fixed low port 14 needs CAP_NET_ADMIN on this kernel. The launcher prebinds FD14 before dropping capabilities. Default logging goes to stdio; A9 does not call qlog_setup/openlog. `lib/qmi.c` is a generic decoder with insufficient short-header/TLV bounds checks; its string decoder writes a terminator after the specified maximum length. The A9 dispatcher therefore validates the complete raw request **before** calling qmi_decode_header or the generic decoder. The unused `qmi_tlv.c` is not substituted for it.

## Related A6L evidence and its limits

The local read-only reference `~/A9-build/ref/hisense-a6l-lineageos/tools/build-radio-userspace.sh` pins the same rmtfs and QRTR commits. `device/hisense/a6l/hals/rootdir/bin/a6l-modem.sh:19` copies **four** stores to tmpfs: modemst1, modemst2, fsg and fsc. Its `hals/rootdir/etc/init/a6l-radio.rc:2`–`:6` reports a 300-second stable modem, WLAN service and Wi-Fi scan with these RAM copies; `:58` runs rmtfs against their directory. This is useful prior evidence that a related Qualcomm modem tolerates in-session writable RAM backing. It does not establish A9 firmware tolerance or prove fsc is necessary on A9.

The A9 map nevertheless includes the golden fsc copy, as explicitly requested after reviewing this evidence. **No A6L seed, calibration or memory address is reused.** The later A6L `device/hisense/a6l/rom/bin/a6l-radio.sh:67`–`:75` opens the real by-name partitions with `-P -o /dev/block/by-name -r`, and a property can remove `-r` to enable write-through. Neither that script nor that property-controlled mode is reused here. A9 preparation uses backup files exclusively, never A6L's live-partition copy step.

## Fixed A9 ABI and implementation

Build with `A9_RMTFS_SAFE=1` in the patched Makefile, or compile the exact A9 source list with `-DA9_RMTFS_SAFE`. The resulting daemon accepts **no command-line options**. Its sole authorized entry point is the reviewed static sandbox launcher. Merely renaming or rebuilding an upstream binary is insufficient.

| FD | Exact purpose | Required validation |
| --- | --- | --- |
| 10 | `/boot/modem_fs1` | O_RDWR regular TMPFS image, 2,097,152 bytes |
| 11 | `/boot/modem_fs2` | O_RDWR regular TMPFS image, 2,097,152 bytes |
| 12 | `/boot/modem_fsg` | O_RDWR regular TMPFS image, 2,097,152 bytes |
| 15 | `/boot/modem_fsc` | O_RDWR regular TMPFS image, 131,072 bytes |
| 13 | qcom_rmtfs_mem1 transport | O_RDWR character device, exact device identity/base/size checked by launcher |
| 14 | RMTFS QRTR socket | AF_QIPCRTR SOCK_DGRAM already bound to port 14; checked by daemon |
| 16 | static MPSS admission and release gate | O_RDONLY fixed 13-byte TMPFS record, configured node 0; controller publishes one gate byte after post-start topology proof |

`a9-storage.c` has no open/openat, partition discovery, read-only fallback or runtime directory configuration. Initialization checks all four types, TMPFS_MAGIC, exact sizes, O_RDWR, absence of O_APPEND, and distinct `(st_dev,st_ino)` identities before enabling any handle. Protocol names match the table exactly. Ten active handles are bound to their caller QRTR node and port; negative/out-of-range IDs, closed handles, unknown names and slot suffixes fail. Receive-side admission uses the explicit A9 node-0 policy on FD16 and a controller release after topology checks, as described in the trial-7 fix below. Protocol CLOSE deactivates its handle without closing the inherited image FD, preserving session writes after reopen.

All image transfers check negative offsets, overflow-safe ranges and per-image length; short/error reads are failures rather than padded success. The dispatcher validates the complete vector against the selected image before transferring any sectors, including the smaller fsc bound. fdatasync applies only to the selected RAM FD and its result is propagated. No ioctl is used.

`a9-sharedmem.c` uses only FD13 with fixed A9 physical base **0x89b01000** and size **0x200000**. The launcher must verify these exact sysfs values at `/sys/class/rmtfs/qcom_rmtfs_mem1/`, along with client_id 1 and the device major/minor. The daemon independently checks O_RDWR and character type but cannot identify that driver from character type alone. Allocation and every pread/pwrite validate the whole physical range using subtraction to avoid overflow; there is no mmap, udev, sysfs, UIO or `/dev/mem` fallback in this source.

`a9-wire.h` rejects short/mismatched headers, wrong message type, unknown/duplicate/missing TLVs, oversized or embedded-NUL paths, nonboolean direction/sync values, malformed arrays and all sector/shared-buffer range errors. Only the five supported request schemas are accepted. The path limit is 255 bytes, leaving room for the generic decoder's terminator. Iovec requests are bounded to 4096 total sectors per batch. Conservative schema restrictions may reject an A9 firmware extension; the correct response is review, not a permissive fallback.

The A9 source list is `qmi_rmtfs.c rmtfs.c a9-sharedmem.c a9-storage.c util.c`. It does not link `rproc.c`, `sharedmem.c`, `storage.c`, qmi_tlv.c, libudev or pthread. It uses the inherited QRTR socket and publishes service 14/version 1/instance 0. No remoteproc start/stop, `-s`, automatic reconnect or storage fallback is compiled into the A9 path.

## Conditional storage proof and external prerequisites

The backend proves a restricted filesystem property: with the validated fixed descriptors unchanged, every EFS write/sync targets one of four regular TMPFS files, and no protocol path becomes a host pathname. The launcher must additionally establish **noswap** on that tmpfs; fstatfs(TMPFS_MAGIC) alone does not distinguish swappable tmpfs or memfd. It must validate same-phone seed hashes/sizes, ownership/permissions, exact shared-device identity, root-owned immutable static executable, no inherited host directory/block FDs, private mount propagation/root/cwd, dropped supplementary groups/capabilities/UID, no_new_privs and seccomp denial of later open/mount/mknod/ioctl/descriptor-replacement/exec escapes. These are required parts of the deployment proof, not capabilities supplied by the storage backend.

The static daemon still has runtime library/socket code, so the sandbox must enforce those prohibitions even if malformed traffic exposes a separate bug. A read-only seed descriptor or a CLI flag alone is insufficient. No real modemst/fsg/fsc/persist partition is opened, even read-only, by the preparation design. Unknown store names remain denied. Any future additional writable store needs an explicit golden-copy FD and source-reviewed size/map change.

Neither this patch nor its tests prove opaque MPSS/TZ hardware isolation, autonomous RF silence, A9 EFS compatibility, persistence across reboot, or stability without other companion services. The A6L reference specifically reports DIAG starvation crashes without a proper diagnostic service; no DIAG binary or service is introduced by this rmtfs patch. Keep modem startup disabled pending the complete reviewed integration and a separately authorized device task.

## Offline validation

Run `CC=<local compiler> NM=<local nm> python3 tests/mainline-wifi/rmtfs.py --sources ~/A9-build/mainline/sol-wifi/src`. The runner copies the pinned source, applies the actual patch, builds the real backend/daemon and executes only host tests. It never opens a QRTR socket, host block device or phone connection, and never reads private seeds.

## First-failure diagnostics (2026-10-16)

The A9 patch emits at most one additional `A9_WIFI_RMTFS_DIAG` record per process,
before the existing fault/exit path. Return values, accepted wire schemas,
caller/peer checks, first-fault state and stop behavior are unchanged. In
particular, an OPEN path with a terminal NUL and an ALLOC with unknown optional
TLVs remain rejected. The record does not imply that a compatibility fix has
been made.

`a9-diagnostics.h` writes a record smaller than 1024 bytes directly to the
existing write-only stderr pipe, without opening a path or changing the
sandbox. It preserves errno and the original result even if logging fails.
`uptime` is CLOCK_BOOTTIME (or -1 if unavailable). `received` means recvfrom was
attempted; `recv`/`recv_errno` record its result/error. Sockaddr fields are valid
only when `address_valid=1`; unavailable addresses are zeroed. `control` is the
libqrtr-decoded packet kind, or -1 before decoding. `message` is a bounded QMI
header preview until `message_decoded=1`; -1 means unavailable. Unknown control
kinds may be mapped to zero by the pinned libqrtr decoder.

`tlvs` contains at most 16 type:length pairs, followed by `capped` if more remain.
`!` marks a declared length exceeding the available bytes; `short-header` marks
an incomplete TLV header. No TLV values, paths, transaction/caller IDs, store
data, addresses from payloads, MAC, IMEI or BT address are printed. QRTR node
and port are transport metadata. Named reasons identify receive/address/sender,
QRTR control/decode, individual wire checks, or handler failures. Boolean/NULL
handler failures use rc=0 with a specific reason; negative results retain their
actual code, and short transfers retain their actual count. Existing fault
markers are still emitted unchanged.

`tests/mainline-wifi/rmtfs-diagnostics.c` reuses the fake transport/backend from
the event-loop tests, runs the real receive/dispatch/QMI decoder, checks valid
OPEN/ALLOC responses and unchanged rejection of terminal-NUL OPEN, optional
ALLOC TLVs, invalid callers and changed peers, and checks a real write-only log
pipe. It also covers reset/control errors, malformed/bounded metadata, the
first-reason latch, errno/log-write failure and 10,000 guard-page metadata
inputs under ASan/UBSan. These synthetic shapes are not captured modem bytes.

No controller change is needed to carry these records: the existing worker
pipe saves stderr in rmtfs.log. The run-2 timing issue is separate: about 157 s
of pre-start work consumed the armed 240 s window, and completion followed
roughly 106 s after MPSS became offline, leaving about five seconds before the
270 s hard deadline. The controller timing follow-up moves full audits before
arming, uses verified process bindings for guarded freshness/normal teardown,
and adds a 15-second start-admission budget without extending either deadline.
See `mainline-wifi-first-trial.md`, "Audit placement and pre-start budget".
The RMTFS diagnostics themselves leave controller behavior unchanged; the
controller follow-up also needs independent review before device use.

The completed GCC 14.3 host run passed AddressSanitizer and UndefinedBehaviorSanitizer checks:

- Real backend on deterministic dummy memfd TMPFS files: write/read-after-write, all four mappings, fsc bound, reopen persistence, isolation between images/nodes, exhaustion, hostile paths/suffixes, unterminated paths, negative/overflow/boundary offsets, closed handles/FDs, missing/truncated/aliased/appending/non-TMPFS files and wrong file type. Block type rejection uses an injected fstat result, not a real block device.
- Actual patched shared-memory implementation with mocked FD13 character-device syscalls: positive round trip and boundary writes, invalid type/mode/fstat, zero/oversize allocation, underflow/overflow/end-crossing/negative ranges and syscall failures. This is an arithmetic/dispatch test, not a target driver test.
- Strict wire checks, every truncation of a valid open, duplicate/oversized/embedded-NUL fields, invalid sectors/buffers, exhaustive one-byte mutations of a valid iovec and 100,000 deterministic malformed inputs. Accepted iovec mutations are passed to the actual pinned generic QMI decoder.
- Full A9 daemon compilation, rejection of every legacy CLI flag before FD use, and symbol checks for absent rproc/shadow/discovery implementations. The event loop and real QRTR/shared device were never started.

LeakSanitizer is disabled because it cannot enumerate threads in the managed execution sandbox; address/undefined-behavior checks remain enabled. No leak-proof claim is made. Target namespace/seccomp/noswap checks belong to the sandbox launcher validation and are not inferred from these backend tests.

## Run 6: multiple client ports on one modem node

`data-source-changed` names the receive-side QRTR endpoint check, before QRTR
decoding, wire validation, header decoding, or backing-store access. It does
not compare store contents, mtime/inode, or shared-memory identity. The run-6
record (`recv=25`, message preview 1, `tlvs=1:15`) has
`message_decoded=0`: those fields are bounded metadata previews. The payload
was not captured, so `/boot/modem_fs1` remains a likely synthetic reconstruction.
The old branch requires an already latched peer; this cannot be the daemon's
first wire-valid DATA packet. Neither the earlier endpoint nor successful
requests were logged, so the archive cannot distinguish a port change from a
node change. Multiple modem client ports are the reproduced failure mechanism.

The initial run-6 fix (`71ebae4`) retained the first wire-valid remote node and
allowed independent client ports on that node. Independent review 71 rejected
that startup admission rule; the following fix supersedes it. A9 backing handles now include
both node and port: OPEN deduplicates only for the same endpoint and image;
CLOSE, RW_IOVEC, ALLOC_BUFF and GET_DEV_ERROR validate that complete owner.
The ten-handle bound is unchanged. BYE or DEL_CLIENT for the latched node is
terminal; DEL_CLIENT conservatively stops the trial regardless of its port.
There is no relatch, reconnect, or retry. Wire schemas, exact logical paths,
fixed RAM descriptors, shared-memory bounds, namespace and syscall policy
remain unchanged. No pathname-open code is added.

`rmtfs-multiport.c` runs the production receive/dispatch/QMI code with the real
A9 storage backend on synthetic TMPFS memfds. It replays consecutive node-0
OPENs from ports 9 and 10, each exactly 25 bytes with one 15-byte TLV. The old
patch fails with the run-6 diagnostic shape; the fix replies successfully,
keeps distinct endpoint callers, accepts each owner's ALLOC/IOVEC, rejects
cross-port use in every caller-consuming handler, preserves close isolation,
and rejects other/local nodes and client loss. QRTR and FD13 I/O are fake.
This is an offline regression, not captured payload replay or modem stability
evidence. Independent review remains required before trial 7.


## Fix round (review 71): independent MPSS admission

The selected kernel does **not** supply a predetermined modem QRTR number:
`net/qrtr/smd.c` registers `QRTR_EP_NID_AUTO`; `af_qrtr.c` learns remote node
IDs from transport headers. DT `qcom,remote-pid` is a SMEM/GLINK processor ID,
not proof of a QRTR node. Node 0 in run 6 is evidence, not a portable default.

RMTFS now uses the same independent identity source as the existing confined
DIAG router: a trusted **local QRTR nameserver** NEW_SERVER notification for
service **4097**, packed instance **1** (version 1, instance 0). The pinned
`diag/router/peripheral-qrtr.c` assigns modem base 0 and CMD offset 1; ADSP and
other subsystem bases differ. The existing `diag.patch:command_server` uses
this exact selector. Kernel `net/qrtr/ns.c:lookup_notify` produces the local
notifications; local processes cannot bind a forged remote source node.
This relies on trusted kernel/nameserver metadata and the existing modem CMD
service assignment, not credentials or authentication of processes inside a
remote subsystem. It does not defend against a remote subsystem forging the
modem's own service advertisement; that is the same metadata trust boundary
used by DIAG, rather than first-RMTFS-sender authority.

Before publishing service 14, RMTFS registers a service-4097/CMD1 lookup over
its existing inherited FD14. A failed lookup prevents publication and READY.
READY still means successful local setup, not completed remote admission.
The exact empty end-of-list notification is harmless and admits nobody.
Only a full-size control packet from the actual local node/control port may
supply admission; its advertised node must be nonlocal/nonbroadcast and port
nonzero/noncontrol. Admission is immutable: changed node or CMD port fails
closed. Matching CMD server deletion, BYE, or any DEL_CLIENT at the admitted
modem node is terminal. There is no DATA-path assignment of trusted identity.

Every DATA request before admission, or from a different/local node after it,
is rejected before wire/QMI decoding, caller lookup, OPEN or storage/FD13 I/O.
Multiple ports at the admitted node retain endpoint-isolated caller handles.
No controller, launcher, policy, descriptor, namespace, syscall allowance,
backing implementation, shared-memory implementation, or wire schema changes.
The only added operation is the lookup on the existing QRTR socket.

If modem DATA arrives before the nameserver CMD announcement is processed,
this candidate fails the trial closed with `data-before-mpss-admission`.
There is no buffering, retry, or assumed startup ordering. Offline evidence
cannot establish that physical firmware ordering; independent review and any
separately authorized later trial must retain that limitation.

`rmtfs-admission.c` tests hostile-first node 2, local/broadcast/other senders,
and even modem DATA before admission across all five request types. Wrapped
storage reads/writes/syncs remain zero and all four sentinel-filled synthetic
stores remain byte-identical. It verifies one-receive production-loop exit,
lookup-before-publication and lookup failure, empty listing, forged control
sources, malformed announcements, subtype/node/port validation, immutable
admission, server deletion, and local1/modem0 plus local0/modem1 mappings.
The trial-6 25-byte OPEN multiport replay now explicitly processes a trusted
CMD announcement before serving ports 9 and 10. No real QRTR/device is opened.


## Fix round (trial 7): static policy with a topology release gate

Trial 7 disproved the startup-order assumption of `6741e68`: MPSS was up at
236.077056 and sent OPEN from 0:9 at 236.092 before any modem DIAG CMD row.
That commit safely refused the request but could not bootstrap the modem.

The user explicitly selected a static A9 policy instead of a kernel change:
MPSS QRTR node **0**, supported by the supplied Qualcomm assignment and the
node-0 measurements in trials 6 and 7. This is configured policy, not runtime
node discovery, a number inferred from the first sender, or an assertion that
SMEM remote-pid1 mathematically maps to QRTR node0. It is scoped to this pinned
A9 kernel/firmware/trial image and must be independently reviewed as such.

The controller creates the root-owned, mode-0600, one-link TMPFS record
`/mnt/a9-wifi/trial/rmtfs.admission` with exactly `A9RMTFS1:0:0\n` before
launching helpers. The launcher verifies those exact pending bytes, ownership,
mode, type, size and TMPFS, opens this one fixed path O_RDONLY with O_NOFOLLOW,
and inherits it as FD16 after jail/capability/UID/seccomp setup. RMTFS validates
its shape/access and parses the policy before storage initialization or READY.
The backing implementation, FD13 transport and wire validator are unchanged.
The auditor admits this one additional read-only preparation path; post-exec
pathname opens and descriptor replacement remain denied. FD16 is retained
only for the RMTFS role and is checked against the controller file's exact
inode with read-only access by every descriptor gate.

Before arming, the controller requires all non-MPSS remoteprocs offline or
absent; no IPCRTR device; no loaded QRTR TUN/MHI transport; matching RPMSG
SMD-driver/device censuses; and only actual-local-node nameserver rows. The
selected MPSS is still identified by compatible, physical 6080000.remoteproc,
firmware path and offline/recovery checks. Its static DT GLINK edge must be
`mpss` with remote-pid1. Missing/ambiguous metadata fails closed.

After the sole start writer returns and MPSS reads running, the controller
repeats the proof with exactly one IPCRTR endpoint bound to qcom_smd_qrtr. Its
real RPMSG parent must resolve to the selected MPSS DT GLINK edge. Every other
remoteproc remains offline; TUN/MHI remain absent; nameserver rows may name
only the actual local node and node0. No modem service or DIAG row is required.
A not-yet-probed MPSS IPCRTR channel is waited for within the **same ten-second
startup deadline**; any other ambiguity is terminal. Before release, monitors,
cancellation flags, state/recovery, pending policy and remaining deadline are
checked again. A one-byte nontruncating write changes only the final gate byte
to 1 in the same inode, with readback.

An early node-0 DATA request is retained in the existing fixed receive buffer
without handler/store access while that gate is pending, for at most ten
CLOCK_BOOTTIME seconds from receipt. Missing/invalid policy, timeout, signal,
wrong/local node or a revoked gate fails closed. Other nodes are rejected
before waiting. The first OPEN therefore no longer needs DIAG publication.
Later modem CMD metadata can check consistency with the configured node; it
cannot select the node or release the gate. Node/port caller isolation and
all existing request/bounds/partition restrictions remain intact.

These are **sampled topology checks plus static node admission**, not kernel
transport authentication. No kernel, DT, module or boot change was made.
A later new endpoint or a remote transport forging source node0 cannot be
cryptographically attributed to a GLINK edge by this daemon. Samples do not
prove continuous exclusivity, erase already queued packets, or protect from
a privileged actor replacing policy/topology. The kernel-binding alternative
would enforce ingress-edge provenance on every packet and avoid those gaps.
Fresh-boot, no-retry, recovery-disabled and trusted-controller assumptions
remain mandatory. Physical first-OPEN response tolerance and actual topology
check latency remain unmeasured offline; expiry fails the trial without
extending any 240/270-second guard or startup budget.

The C replay waits 15 ms after synthetic up, sends the exact 25-byte OPEN
shape with no nameserver row, asserts zero store I/O while a simulated
post-start proof is pending, then releases the gate and verifies both modem
client ports. The suite retains hostile-first and cross-port tests, checks
all four sentinel stores byte-for-byte, exercises the ten-second timeout and
malformed/stale/writable/missing policy, and runs actual launcher admission
and descriptor closure on synthetic memfds (root metadata injected).
Controller regressions execute real proof/parsing/file-write code over fake
sysfs/NS fixtures, including extra/unknown edges, online other processors,
TUN/MHI, bad NS rows, wrong admission inode/access, returned-writer ordering,
and the shared startup budget. Unit fixture resets do not model retries.
