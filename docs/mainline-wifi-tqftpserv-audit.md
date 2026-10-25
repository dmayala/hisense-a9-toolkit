# A9 tqftpserv source audit

2026-10-16. Offline source review and host tests; no phone, QRTR daemon, real
partition, persist mount, firmware execution or network operation was used.
Reviewed upstream commit `b31de6db4f31d09119293203d39636b827f16321` from
`~/A9-build/mainline/sol-wifi/src/tqftpserv`. Line numbers below refer to that
unpatched commit. The patch is `scripts/mainline/wifi/patches/tqftpserv.patch`.

The A9 build admits three exact virtual names through preopened descriptors:

| Virtual filename | FD | Access and cap |
|---|---:|---|
| `/readonly/firmware/image/wlanmdsp.mbn` | 20 | Readonly, exact 3,814,580-byte pinned firmware |
| `/readwrite/mcfg.tmp` | 21 | Existing regular tmpfs RAM scratch, reads/writes, maximum 1MiB |
| `/readwrite/server_check.txt` | 22 | Separate existing regular tmpfs RAM scratch, reads/writes, maximum 1MiB |

A6L reference `tools/build-radio-userspace.sh` pins tqftpserv
`c2559a26098f6d2b36a946ef0b6ad02223264c3e`. We retain the audited newer `b31de6d`
for a concrete security reason: its diff fixes short writer-packet length underflow
before memcpy, bounds reader ERROR termination, checks allocation failure and
closes `//` namespace escapes/trailing `..` traversal. The A9 patch excludes the
upstream path resolver and additionally bounds its active protocol handlers.
Neither upstream pin alone supplies the required confinement.

A6L `device/hisense/a6l/hals/rootdir/etc/init/a6l-radio.rc:19` and
`docs/archive/radio-prep-20260923.md` identify these two scratch names on tmpfs.
That is related-board compatibility evidence, **not proof of A9 requests or modem
behavior**. Every other readwrite name, every unknown readonly name and every
unsupported creation/truncate/append flag combination is denied. No EFS or persist content seeds these two
blank scratch files. Firmware behavior under this allowlist remains unverified.

## File interface and source findings

| Source | Complete relevant behavior | A9 disposition |
|---|---|---|
| `translate.c:22–36,245–277` | Prefixes `/readonly/firmware/image/`, `/readonly/firmware/modem_pr`, `/readonly/vendor/firmware/`, `/readonly/vendor/firmware_mnt/image/`, `/readwrite/`; Android firmware base `/vendor/firmware/`, RW base `/data/vendor/tmp/tqftpserv`; Linux bases `/lib/firmware/` and `/var/lib/tqftpserv`. | Entire upstream translation implementation is excluded by `A9_TQFTP_SANDBOX`. Only the three full case-sensitive names above exist; all alias roots, directories and vendor/modem_pr paths fail. |
| `translate.c:40–52,69–167` | `fopen`/`fread` the firmware_class sysfs parameter; open remoteproc class, fdopendir/readdir, openat firmware attributes, read directory name; try custom firmware path, `updates/`, then default firmware base. | No sysfs, firmware directory scan, environment or fallback. Source has an uninitialized `fw_sysfs_path` when sysfs read fails, and `firmware_value[n]=0` can overrun when read fills PATH_MAX. These branches do not exist in the A9 preprocessed object. |
| `translate.c:178–200` | mkdir RW base, open it as a directory, then `openat(base, client_name, flags,0600)`. | Excluded. Two existing preopened RAM inodes replace the entire RW directory; no mkdir/open/create operation exists. |
| `translate.c:218–241` | Rejects `..` components and repeated `/`. Does not prevent symlink traversal or hardlinks; opens have no `O_NOFOLLOW`. `modem_pr` prefix matching lacks a component boundary. | Replaced with exact bytes; no normalization or client-controlled relative path reaches a filesystem operation. Symlink races cannot redirect an already admitted descriptor. |
| `translate.c:289–319` | `access` then plain readonly `open`; `.xz` fallback, then `.zst` fallback. All resolve symlinks and have separate check/open operations. Readonly translation ignores incoming write flags and returns a readonly fd. | Excluded, not merely assumed safe because named readonly. No compressed filename, suffix fallback, path stat or access probe exists. |
| `tqftpserv.c:181,501,510–519` | RRQ calls translation with O_RDONLY, uses lseek to obtain tsize/reset offset and pread to send file ranges. | Retained against a checked duplicate of the exact mapped capability; fd20 additionally requires expected hash and immutable copied backing. Size-error/socket-setup cleanup paths now close duplicates. Shared file-position changes do not affect pread/pwrite. |
| `tqftpserv.c:651,891` | WRQ requests O_WRONLY\|O_CREAT (no O_TRUNC). Writer calls write, ignores seek, acknowledges any nonnegative short write; no fsync/fdatasync/truncate/rename/unlink anywhere in daemon. Existing longer files retain trailing data. | A9 maps that exact WRQ flags combination only to fd21/fd22. Existing RAM files are neither created nor truncated. All actual writes use bounded `a9_translate_pwrite`, retry partial/EINTR writes, and advance a per-session position. Firmware fd20 rejects all write requests. |
| `xz-decompress.c` | stat/open/mmap compressed pathname, LZMA decoder with UINT64_MAX memory limit, memfd_create and repeated write of unbounded decompressed output; error cleanup, successful return without rewind. | Not compiled or linked. No compression dependencies. |
| `zstd-decompress.c` | stat/open/mmap, trusts frame size for allocation, decompresses to heap, memfd_create/write; successful return leaks heap and input mapping; write uses advertised size rather than checking actual decompressed byte count. | Not compiled or linked. A9 accepts the uncompressed pinned WLANMDSP only. |
| `translate.h`, `xz-decompress.h`, `zstd-decompress.h` | Translation prototypes; optional decoder declarations or stderr/error stubs. | Only translation header plus new init/positioned-write prototypes used. Decoder headers excluded with original translation. |
| `list.h` | Intrusive list helpers only, no IO or filesystem calls. | Unchanged. Client list sizes bounded in A9 mode. |
| `populate-readwrite.sh:6–21` | Opens real persist via mount `/dev/disk/by-partlabel/persist` RO, recursively copies `/rfs/msm/mpss/*`, creates ready marker, unmounts. Requires privilege, exposes broad persisted contents and makes host copies writable. | **Never run or install**, including its oneshot systemd unit. This draft does not mount or populate persist at all. |
| `tqftpserv.service.in`, `tqftpserv-populate-readwrite.service.in` | Ordinary upstream service and privileged population helper. | Never install. Only the project sandbox launcher may start the A9 binary, and startup remains disabled by default. |
| `meson.build`, `meson_options.txt`, `Android.bp` | Build options, optional auto-detected LZMA/ZSTD and systemd installation, default translation. Android.bp links shared qrtr. | Direct static build of **only** `tqftpserv.c` and `translate.c`, `-D_GNU_SOURCE -DA9_TQFTP_SANDBOX`, pinned static libqrtr and libc. No upstream install step, compression feature or shared loader. |
| `ci/*.sh`, `.github/*` | Host package installation/build CI; Arch script clones qrtr, Android script writes cross file/builds/installs, decoder checks inspect build objects. | Reviewed as build tooling; not invoked or shipped. No CI/network command is part of the offline runtime or host tests. |

## Descriptor contract

At startup `argc` must be exactly 1 and `a9_translate_init()` must validate all
three descriptors before the first QRTR socket. Every translation and write
rechecks all roles and rejects any shared device/inode between them. Every
descriptor must be a regular file with no O_PATH/O_APPEND; fd20 must have link
count exactly 1, be O_RDONLY and have exact firmware size. Each RAM descriptor
must have link count 0 or 1, O_RDWR access, size 0–1MiB, and successful fstatfs
reporting TMPFS_MAGIC. This accepts real anonymous memfd test fixtures (nlink0)
and named private tmpfs launcher files (nlink1), and rejects disk-backed regular
files even with correct size/mode. Null/unknown/normalized/alias paths and
unsupported flags fail EACCES. Translation accepts exactly O_RDONLY, or exactly
O_WRONLY|O_CREAT for one of the two RW mappings. O_CREAT expresses the protocol
request only: no creation syscall is performed. A successful lookup duplicates
the preopened descriptor and sets FD_CLOEXEC.

Every write independently checks that its actual fd is O_RDWR without O_APPEND
and that fstat device/inode matches a currently valid admitted RW descriptor.
Negative, overflowing or end-beyond-1MiB ranges fail before pwrite. The helper
retries EINTR/short pwrite and only acknowledges a complete write. Each writer's
`seek` tracks its own position; duplicates cannot interfere through the shared
open-description offset. Reads use pread as upstream. Overlapping explicit ranges
from separate sessions can overwrite the same RAM bytes by design; that is not a
path escape. No truncation or fsync occurs, and shorter overwrites preserve old
RAM tails as upstream did. All these changes are lost when the private RAM files
are discarded.

The launcher remains responsible for opening the expected copied firmware with
O_NOFOLLOW, checking SHA256
`cddbe8e630c1f78d09c66f4b2177a7e0d2e63ec9ce59c47dad5734725df728aa`,
precreating two **blank**, distinct scratch files only on noswap/noexec private
RAM, checking mount provenance, closing other descriptors, excluding writable
aliases for fd20, and denying pathname
open syscalls after setup. Descriptor metadata alone cannot establish original
pathname provenance or stop another privileged process mutating the inode.
Read-only mounts, dropped privileges/capabilities, private namespace and launcher
digest verification are required parts of this contract. fstatfs proves tmpfs
backing, while the launcher separately proves the noswap/noexec mount options.
This daemon opens no
real block partition, persist, EFS, `/dev`, `/proc` or `/sys` path.

## Protocol and resource audit

The listener publishes QRTR TFTP service 4096/version1/instance0. Only `-d/-h`
exist upstream; A9 rejects all arguments. RRQ filename and mode and each option
pair use bounded NUL searches. Octet mode is required. Options are blksize,
timeoutms, tsize, rsize, wsize, seek; unknown options are ignored as upstream.
A9 requires nonempty ASCII decimal values even for unknown options, blksize
512–65464, window 1–32, seek 0–16MiB, timeout 1–255000ms, rsize 1–100MiB and
nonnegative tsize. WRQ seek additionally cannot exceed 1MiB. Duplicate options
still resolve to the last value. These constraints bound allocation to roughly
2MiB per client and at most 16 combined readers/writers;
the launcher must additionally set memory/FD/CPU limits.

Incoming reader ACK/ERROR packets require 4 bytes and matching QRTR node/port.
Main ERROR dispatch previously read byte 2/3 for a 2/3-byte packet; the patch
rejects that case. Opcode and block decoding now casts bytes unsigned before
shifting. A9 bounds main and client socket numbers below FD_SETSIZE before any
FD_SET. Requests above the combined client cap are dropped. Unknown/readonly WRQs
fail before allocating a writer. A9 WRQ without options starts with ACK0, fixing
upstream code that attempted to send DATA on an O_WRONLY descriptor.

Upstream `rsize=0` incorrectly ended ordinary reads after the first ACK; its
uint16_t window loop could wrap indefinitely. The A9 reader uses a bounded
unsigned loop, handles default full reads, short/zero terminal blocks, requested
ranges and EOF acknowledgement. Minimum blocksize prevents rollover for this
single <4MiB file, and suspicious block/offset arithmetic is rejected. These are
host-tested changes, not a claim of full RFC conformance or MPSS compatibility.

Upstream has no implemented idle timeout: `timeoutms` is negotiated but select
waits indefinitely. Client removal for QRTR BYE/DEL_CLIENT only traverses writers,
so stale A9 readers can consume the bounded 16 slots until restart. This is an
availability limitation, not access to persistent storage. No remote firmware
path request can widen the allowlist. Future required read or write names need
new source evidence, explicit preopened mappings and host tests; do not restore
directory fallback merely to make firmware start.

## Validation

Run `bash tests/mainline-wifi/tqftp-run.sh` with CC/NM and the pinned local source
trees. The script copies source into a temporary directory and applies this
patch with zero fuzz. It compiles the actual A9 translation object and rejects
imports of path APIs or unpositioned file writes; bounded pwrite is allowed. It links that object to tests for uninitialized
state, missing/nonregular/wrong-size/RW/O_PATH descriptors, unknown virtual paths,
unsupported flags, readonly write requests, symlink replacement, hardlinks,
admitted RW writes on real anonymous tmpfs memfds, rejection of a real regular
non-tmpfs scratch file, rejection of aliased role inodes, signed/overflowing/cap-crossing
writes, unchanged shared file offsets and unchanged sentinel contents. The host
run used `/tmp` on an ext filesystem for its rejection fixture; no `/dev/shm`
write or tmpfs mount was needed. It also includes the actual patched protocol implementation
with QRTR/network stubs, testing bounded options, malformed fields, read windows,
EOF/rsize/seek behavior, rollover rejection, actual bounded RAM WRQ and
unknown/readonly write refusal. It never
starts the daemon event loop or opens a QRTR socket.

Host GCC tests passed normally and with AddressSanitizer+UndefinedBehaviorSanitizer.
For this sandbox ASAN_OPTIONS=detect_leaks=0 was necessary because LeakSanitizer
cannot use its ptrace support here; no leak-detector result is claimed. Root's
launcher/static ARM64 build and final tests are separate integration checks.

Source-interface audit outcome: **the A9 file interface is closed to one
readonly firmware capability and two bounded RAM scratch capabilities**.
The named-write requirement is included from explicit A6L evidence, without
exposing a directory or arbitrary file creation. A9 firmware behavior remains a
future compatibility gate; additional names require evidence and another audit.

## Review 23 follow-up: first-fault stop and session lifetime

The restricted first-trial build latches `A9_WIFI_FAULT tqftpserv` on every
refusal/error response, including a refusal whose reply transport cannot be
created. Request/parser/range failures, capacity exhaustion, peer loss, short
read/send/write, RAM sync errors, receive/decode/select errors and expiry exit
the service with failure. The coordinator observes both markers and process
loss and stops MPSS; the daemon cannot start/stop remoteproc or retry itself.
A successful vendor end-of-transfer notification is handled as completion.

Each reader/writer records CLOCK_MONOTONIC start and last progress. The negotiated
idle timeout is at most 10000 ms (default1000 ms); total session lifetime is
60000 ms. `select` has the nearest deadline, capped at1000 ms, and expiry runs
both before and after waiting. Thus a silent client expires without another
packet. Both reader and writer lists use the decoded QRTR client node/port for
BYE/DEL_CLIENT cleanup. Active client disappearance ends the first trial.

Tests now establish real successful RRQ and WRQ setup with fake host transports,
all16 session slots, idle and absolute expiry, both-list cleanup, rejected
firmware/unknown writes with available and failed transports, short send/read,
RAM sync failure and actual main-loop exit after one fault or timer expiry.
They use dummy regular/memfd inputs, fake time and transport calls; no QRTR
socket or ARM64 executable runs. Bounds/storage invariants remain independently
tested. These changes do not add an export, backing path or fallback.
