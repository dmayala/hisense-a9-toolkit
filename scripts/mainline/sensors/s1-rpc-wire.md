# S1 FastRPC transport: artifact-derived wire contract

Offline reconstruction, 2026-10-17. The C implementation is original code using
the upstream Linux 7.2.10 FastRPC uAPI and the narrowly decoded Qualcomm reverse
call protocol. It does not wrap stock libadsprpc, alias vendor device nodes, or
translate vendor ioctl numbers. No stock code, firmware, persist data or device
identifiers are included. This document records static protocol evidence; no
ADSP execution or firmware interoperability has been measured in this work.

Private artifacts/complete REA results are retained under
`~/A9-build/mainline/sensors-s1-evidence/`. Provider: Ghidra 12.1.4. Ghidra imports
the compressed debug symbol table, so internal function names below were observed
rather than assigned from strings. Its pseudocode is reconstructed, has known
type-propagation warnings, and does not establish execution.

## Actual sensors attachment, correcting the URI-only inference

Stock `libssc_default_listener.so` SHA256
`5948102129bd0ea4997546ac1c600c72ef87e920a4d4353b66614895d07b0574`:
REA `adsp_default_listener_start`, evidence
`ev_71d654c291ae3626ce83706608862efa05968c794888390866bb4e51bd6a7365`,
constructs the special `createstaticpd:sensorspd` URI, calls remote_handle_open,
opens `adsp_default_listener`, invokes its method 0, and waits for a local listener
eventfd. **The URI name does not mean the sensors startup uses CREATE_STATIC.**

Stock `libadsprpc.so` SHA256
`9d41844fc352f0dadea616aa0555d6a683bf7f2bbe43585642e0c9d5827cf3cc`:
`remote_handle_open_domain` at REA VA0x1206d0 intercepts the special URI and sets
mode 2 for sensorspd. `open_dev` selects downstream INIT flags 3 for that mode.
Its audiopd/mode1 branch instead selects flags2, a process name, and 2 MiB heap.
The sensor path is the SNS attach path, corresponding to upstream
`FASTRPC_IOCTL_INIT_ATTACH_SNS` (0x5208); it needs no invented sensor ELF or
CREATE_STATIC heap. Complete evidence is `rea-remote-open-domain.json` and
`rea-open-dev.json`. The upstream driver directly assigns SENSORS_PD and invokes
root initialization; this is not proof the firmware accepts it on this hardware.

Use exactly the existing gated ADSP misc descriptor, opened/validated by the
runtime owner. The transport never opens a path. Upstream INVOKE is ioctl
0xc0105203: `{u32 handle,u32 scalar,u64 args}`; each argument is
`{u64 ptr,u64 length,s32 fd,u32 attr}`. All arguments here use fd=-1,attr=0, so the
kernel copies ordinary user buffers through its mapped bounce allocation. The
userspace listener requires no vendor ION ABI or userspace DSP mmap operation.

## Root module and listener calls

`remote_handle_open_domain` observes root module handle0, scalar0x00020200:

| Argument | Meaning |
|---|---|
| in0, 8 bytes | u32 name length including NUL, u32 error-buffer capacity |
| in1 | NUL-terminated module name |
| out0, 8 bytes | u32 returned module handle, u32 dynamic-loader error |
| out1 | error string buffer |

The runtime attaches SNS, initializes listener handle3, starts the reverse-loop
thread, then opens `adsp_default_listener` through handle0 and invokes the returned
handle's method0/scalar0. A successful open does not prove registry delivery.

`listener_start_thread.cfi` at REA VA0x124df8, evidence
`ev_3045d8e4c9f8453887a642c444a24f1935f3f6a052bad7018a550816e50ecda2`
(`rpc-part0.json`) observes:

- handle3, method3/init2, scalar0x03000000, no arguments;
- handle3, method4/next2, scalar0x04020200, two inputs/two outputs;
- optional method5/get-in-buffers2, scalar0x05010200, used by stock when its
  receive buffer must grow. S1 instead supplies a fixed 64 KiB buffer and refuses
  larger requests; it never retries/grows an unbounded remote length.

next2 arguments:

| Argument | Meaning |
|---|---|
| in0, 16 bytes | u32 previous context, i32 previous result, u32 previous packed reply length, u32 receive capacity |
| in1 | previous packed reply, length zero when previous result is nonzero |
| out0, 16 bytes | u32 request context, u32 AP reverse-module handle, u32 request scalar, u32 required packed input length |
| out1 | packed request |

Initial context=0, previous result=-1, previous reply length=0 match stock.
Scalar buffer counts occupy bits16..23 (input) and8..15 (output). Method occupies
bits24..28. Low-byte input/output handles and high reserved bits are refused.

For every input buffer: little-endian u32 length, then (only if nonempty) pad the
cursor to the next 8-byte offset relative to the packed buffer, then payload.
After all input buffers, each output buffer contributes only its u32 capacity.
The reply independently packs each output as u32 length, alignment to8 if
nonempty, then payload. Empty output buffers still contribute four length bytes.
`pack_out_bufs` evidence
`ev_497792efdf96e83fb8f56180b73f434a8bbbf4ed9353d8743cfbba18f08a69ea`
corroborates the reply rules (`rea-pack-out.json`). This is the measured Qualcomm
frame, not a custom test protocol. Input count, total capacity, arithmetic,
truncation, string termination and trailing bytes are checked before dispatch.

## AP reverse modules and file operations

Stock listener initialization registers apps_remotectl at constant handle0 and
apps_std as a static module. apps_remotectl method0/open and method1/close are
observed by REA at VA0x12d7c8, evidence
`ev_3c5a2f860ba8ead489424b9971e274b47abb0132a31cd364d80c7ab407a9ce1b`.
S1 only admits the exact module names `apps_std` and `sns_registry`, returning
opaque module values0x100/0x101. A caller-chosen reverse handle is never a host
descriptor or library name passed to dlopen. Unknown modules fail closed.

`apps_std_skel_invoke.cfi` at REA VA0x126be8 (`rea-apps-std.json`) measures these
shapes. NI/NO counts are exact in the S1 implementation, which is stricter than
the stock skeleton's minimum-count checks.

| Method | Operation | NI/NO | Arguments |
|---|---|---|---|
| 0 | fopen | 3/1 | in0={pathlen,modelen}, in1=path, in2=mode; out0=u32 handle |
| 2 / 3 | fflush / fclose | 1/0 | in0=u32 handle |
| 4 | fread | 1/2 | in0={handle,count}; out0={actual,eof}, out1=bytes |
| 5 | fwrite | 2/1 | in0={handle,count}, in1=bytes; out0={actual,eof} |
| 8 | ftell | 1/1 | in0=handle; out0=i32 position |
| 9 | fseek | 1/0 | in0={handle,i32 offset,i32 whence} |
| 10 | flen | 1/1 | in0=handle; out0=u64 size |
| 11 | rewind | 1/0 | in0=handle |
| 14 | clearerr | 1/0 | in0=handle |
| 16 | getenv | 2/2 | in0={namelen,capacity}, in1=name; out0=u32 required length, out1=value |
| 19 | fopen_with_env | 5/1 | in0={envlen,delimiterlen,pathlen,modelen}, then four strings; out0=handle |
| 22 | fileExists | 2/1 | in0=pathlen, in1=path; out0=one-byte boolean |
| 23 | fsync | 1/0 | in0=handle |
| 24 | remove | 2/0 | in0=pathlen, in1=path |
| 26 | opendir | 2/1 | in0=pathlen, in1=path; out0=u64 opaque directory handle |
| 27 | closedir | 1/0 | in0=u64 directory handle |
| 28 | readdir | 1/1 | in0=u64 directory handle; out0=264-byte dirent |
| 29 | mkdir | 2/0 | in0={pathlen,mode}, in1=path; mode is constrained by the RAM broker |
| 31/sub31 | stat | 2/1 | in0={31,pathlen}, in1=path; out0=96-byte metadata |
| 31/sub33 | rename | 3/0 | in0={33,fromlen,tolen}, in1=from, in2=to |

readdir's result is u32 inode at offset0, NUL name at4 (maximum254 bytes),
and u32 EOF at260. Directory handles are generation-tagged broker capabilities,
not the stock host DIR pointers. fileExists returns success plus false for a
normal missing path; broker policy refusal still terminates the listener.

The extended stat reply is stock `apps_std_STAT` (96 bytes): `tsz`=0 at +0, `dev`+8, `ino`+16,
**`u32 mode`+24, `u32 nlink`+28, `u64 rdev`+32**, `size`+40, atime seconds/nanoseconds +48/+56, mtime +64/+72,
and the **ctime nanoseconds word stored twice** at +80 and +88 (stock quirk, preserved). Source: stock serializer at VA
`0x298e4..0x29918` and the decompiled `apps_std_stat` (`apps_std_imp.c:1162-1165`; it opens the name with
`apps_std_fopen_with_env("ADSP_LIBRARY_PATH",";",name,"r")` and `fstat`s the stream, so directories stat fine; failure returns errno, or 0x201
when none). **Correction (S1 attempt 4):** an earlier version of this paragraph read the REA decompilation's struct labels literally
(`nlink`@24, `gid`@32). Ghidra had typed the buffer with the x86-64 `struct stat` although the library is AArch64; on AArch64 the
8 bytes at `st+16` are `{st_mode, st_nlink}` and those at `st+32` are `st_rdev`. Sending `st_nlink` where the PD expects the mode made the
registry directory look like mode 2, and the sensors PD asserted in `SNS_REG_INIT` (`sns_registry_sensor.c:94`). The implementation uses
metadata from a broker-pinned RAM inode and never stats a real persist path.

**Path folding (S1 attempt 5).** Stock hands the PD's path to the host libc, so the kernel resolves `..`. The sensors PD relies on that:
right after the registry directory it stats `/mnt/vendor/persist/sensors/registry/registry/../sns_reg_version` (the `output=` directory
from `sns_reg_config` plus `"/../"` + `"sns_reg_version"`; the PD image also carries a `"/../../"` variant, adsp.b15 strings at VA
`0xb22247b9`, `0xb2224bef`, `0xb2224e60`). The broker therefore folds `..` lexically on the absolute remote path before the
unchanged prefix allowlist and lexical rules run: a `..` may not climb above `/`, and `.` or empty components are still refused
(also next to `..`, so a fold never disagrees with the kernel). The folded path reaches nothing a direct request could not, keeps
the read-only/writable split of its prefix, and is still opened with `openat2` BENEATH/NO_SYMLINKS/NO_MAGICLINKS/NO_XDEV. Known
divergence from the kernel, safe direction: folding does not require the popped component to exist or be a directory, and
never follows a symlink there (the RAM tree has none).

### Layout audit of the other AP file methods (stock skeleton `apps_std_skel_invoke` at VA 0x126be8 vs this implementation)

Checked against the saved decompilation and `apps_std_fread`; all match except stat above, which is fixed.

| Method | Stock | Ours |
|---|---|---|
| 0 fopen | in0 `{pathlen,modelen}` (u32,u32), in1 path, in2 mode; out0 u32 handle | same |
| 4 fread | in0 `{handle,count}`; out0 `{actual,eof=feof()}`, out1 buffer >= count; short read sets eof | same (`eof = actual < count`) |
| 5 fwrite | in0 `{handle,count}`, in1 bytes; out0 `{written, feof()=0}` | same |
| 8 ftell / 9 fseek / 10 flen | in0 handle -> out0 i32; in0 `{handle,off,whence}`; in0 handle -> out0 u64 | same |
| 16 getenv | in0 `{namelen,capacity}`, in1 name; out0 u32 length, out1 value | same |
| 19 fopen_with_env | in0 `{envlen,delimlen,pathlen,modelen}`, four strings; out0 u32 handle | same |
| 22 fileExists | in0 pathlen, in1 path; out0 one byte (`stat()==0`) | same |
| 24 remove | in0 pathlen, in1 path; errno | same (files in the writable RAM tree) |
| 26 opendir / 27 closedir | in0 pathlen -> out0 u64 handle; in0 u64 handle | same, handle is an opaque broker capability |
| 28 readdir | in0 u64 handle; out0 >= 264 bytes: `ino` u32@0, name@4 (max 254 chars + NUL), `eof` u32@260 (empty entry, eof 1 at the end); `.` and `..` are returned like libc | same |
| 29 mkdir | in0 `{pathlen,mode}`, in1 path; errno (EEXIST etc.) | same, mode constrained by the broker |
| 31 sub31 stat | see above | fixed |
| 31 sub33 rename | in0 `{33,lenfrom,lento}`, in1 from, in2 to; errno | same |
| 30 rmdir, 31 sub32 ftruncate, others | exist in the stock skeleton | **not supported on purpose** (fail closed, traced). The sensors PD image's registry code references stat/opendir/readdir/mkdir/rename/fopen/fread/fwrite/fclose and "retrying" loops, not rmdir or ftruncate. |

The trial's environment namespace is deliberately limited to ADSP_LIBRARY_PATH
and DSP_LIBRARY_PATH. Both return the explicit RAM configuration search list
`/vendor/etc/sensors;/vendor/etc/sensors/config`. fopen_with_env accepts only those
names and the semicolon delimiter, tries the per-domain adsp subdirectory then
the base directory, and resolves absolute paths through the same broker. Stock
`apps_std_getenv` returns -1 for an absent variable; S1 preserves that result for
other names. This is trial configuration, not a claim that stock used this search
list. No caller can mutate the process environment or select a host search root.
DSP library paths outside the prepared RAM namespace remain unsupported and stop
the trial; required unseen DSP libraries must be admitted as explicit read-only
RAM copies after evidence identifies them.

Observed stock apps_std methods return POSIX errno values on file failures:
fopen clears errno, calls fopen, returns errno (or -1 if none was set) on failure;
read/write use the same pattern. The transport preserves the broker's errno.
Malformed wire uses AEE_BADPARM14, unknown method20, unknown module512 as observed
in the corresponding skeleton/table. It does not map all errno values through an
invented AEE enum.

## sns_registry property bridge

`libsns_registry_skel.so` SHA256
`58016077c77732068123cab30236efbfd8e76a29990731fb24fe2736a8ff2770`:
`sns_registry_skel_invoke` method0 has two input buffers and one output: in0
`{u32 name_length,u32 value_capacity}`, in1=NUL name, out0=value string.
`sns_registry_get_property` rejects capacity<92, clears the output, calls Android
property_get with the empty-string default and copies the result. Retained REA
records: `rea-sns-property.json`, `rea-sns-get-property.json`.

S1 intentionally provides an empty property namespace, matching that measured
absent-property/default case. It does not copy host environment, query an Android
property server, or fabricate hardware identity. Whether a populated vendor
property is necessary for successful SEE startup remains a hardware question;
an empty result must not be described as a measured stock value.

## Completion and verification boundary

The caller owns confinement, the gated descriptor, thread ordering and ADSP
shutdown. No timeout closes a descriptor still in use by ioctl. The listener
checks a 180 s monotonic limit between exchanges and caps requests at 4096; the external
guardian stops ADSP and joins/reaps the process to prove actual completion.

`S1-REGISTRY-DELIVERED` is printed only after a successful next2 exchange following
successful broker persist reads/writes. A local RAM read alone, or EPIPE on the
next exchange, prints no delivery marker. No paths, filenames or data are logged.
READY, attachment, PD UP, SNS listing and registry delivery are separate evidence.

`tests/mainline-sensors/test-s1-rpc-wire.py` runs the implementation against an
explicit mock upstream ioctl endpoint on native x86_64 and static AArch64/QEMU.
It checks exact scalars/headers, file-method layouts, every truncation boundary,
oversize/handle/reserved-bit refusals, strings/modules, and positive/negative
response-delivery coupling. These tests validate serialization/control flow and
do not claim to execute the sensor firmware.

`test-s1-listener-integration.py` additionally composes the actual RPC code, RAM
broker, confinement and a 1 MiB pthread stack inside a disposable user namespace.
Its only simulated boundary is the ioctl endpoint. A real copied RAM file is
opened/read through the broker; external open/socket attempts remain denied.
The no-ACK case successfully reads RAM but returns EPIPE before the next exchange
and must emit no delivery marker. The ACK case must emit one only after the next
exchange succeeds. The native glibc fixture disables optional rseq registration
through `GLIBC_TUNABLES=glibc.pthread.rseq=0`; the target uses static musl and no
rseq syscall was added to the production allowlist. clone3 is denied with ENOSYS
to let native glibc fall back to the already permitted thread-only clone path.

Final native x86_64, static AArch64/QEMU wire, and composed tests all pass. Logs
and a source-bound `rpc-provenance-receipt.json` are in private
`~/A9-build/mainline/sensors-s1/test-evidence/`. The composed fixture exercises
the production RPC/sandbox/broker functions and runtime sequencing; phone boot
identity/device bootstrap and a real FastRPC driver/firmware remain unexercised.
Hardware service readiness remains unmeasured.
