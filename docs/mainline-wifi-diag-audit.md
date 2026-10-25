# Confined DIAG for A9 MPSS bring-up

2026-10-16. Status: source-audited, host tested, phone behavior unverified.

The first SM6115 trial must have the confined modem-only DIAG service ready
before the explicit MPSS start. This is a conservative dependency inferred from
the A6L result, not a claim that A9 DIAG starvation has been reproduced. Do not
start without DIAG simply to try to provoke that failure.

## Evidence and credit

The read-only [Hisense A6L project](https://github.com/PaddleStroke/hisense-a6l-lineageos)
reference at `ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d` documents the approximately
100-second `dog_hb Task starvation: diag` failure and the QRTR solution in
`docs/archive/radio-diag-fix-20260923.md`, and makes DIAG readiness mandatory in
`device/hisense/a6l/hals/rootdir/bin/a6l-modem.sh` and `a6l-radio.rc`.
The original RPMSG-only experiment did not solve it; forcing DIAG glink channel
creation caused firmware assertions and must not be repeated. Its working
transport publishes apps-side service 4097 instances 0/2/4 and looks up modem
command instance 1. The DIAG instance occupies the service version byte, not
ordinary QMI's instance packing. No DIAG glink channel is added here.

Static fixed-string checks of A9's already-private golden copies found
`diag_socket` once, `diag_task` twice, and `diagdiag` three times in MODEM.B19;
`dog_hb` and `Task starvation` occur in MODEM.B04/B19. Only fixed term names and
counts were printed, no firmware payload or identity. This establishes DIAG
implementation in A9's firmware; it does **not** establish the endpoint number,
transport routing, or watchdog dependence. The initial explicit test must
observe the command peer/control handshake and at least 180 seconds of healthy
remoteproc state. The local node is obtained from getsockname, not assumed. The trusted local
nameserver's service4097/CMD1 announcement selects the remote modem node, which
must differ from the local node. Tests cover local1/remote0 and local0/remote1.

A6L uses SDM660 `qcom_q6v5_mss`/MBA; A9 uses SM6115
`qcom_q6v5_pas` PAS4 with split modem.mdt. That authentication/startup difference
does not prove a different DIAG transport. Neither daemon starts remoteproc.
This QRTR diagnostic service is unrelated to the protected on-disk partition
named `diag`; it never opens that partition or any block device.

## Source identity and complete reachable-surface audit

GitHub source fetched only from `https://github.com/linux-msm/diag.git`, pinned
to A6L's `23c12c167e93215853af4e59c021551767f6fec8`. The exact tree and
`git archive --format=tar` SHA256 are in `scripts/mainline/wifi/sources.lock.json`.
Upstream source stays detached and unmodified in the private build cache.
`diag.patch` is applied to an exported build copy, with zero patch fuzz.

| Upstream files | Entrypoints, effects and disposition in A9 |
|---|---|
| Makefile, README, doc/, LICENSE | Default Makefile links all router clients and `send_data`. A9 build bypasses that source list, preserves license, and compiles only `router/a9-router.c` plus reviewed libqrtr send/publish helpers. |
| router/diag.c | `-s` TCP host and `-u` UART options; always tries `/dev/ffs-diag`, abstract Unix DIAG socket, peripheral discovery, general command tables. Entire translation unit excluded. A9 accepts no arguments. |
| router/socket.c | IPv4/TCP socket, DNS `gethostbyname`, external connect and client attachment. Excluded; seccomp also rejects IP sockets. |
| router/unix.c | Abstract `\0diag` SOCK_SEQPACKET server, listen/accept and arbitrary local diagnostic clients. Excluded; seccomp rejects Unix sockets/listen/accept. |
| router/uart.c | Arbitrary option-selected UART `open`, termios ioctls and read/write transport. Excluded; no option or device open exists in A9 daemon. |
| router/usb.c | FunctionFS directory `open`, ep0/ep1/ep2 `openat`, descriptor writes and AIO traffic. Excluded; no FunctionFS capability/FD reaches jail. |
| router/peripheral-rpmsg.c/.h | udev enumeration and monitor, sysfs names and arbitrary discovered RPMSG `open`; glink backends. Excluded, no udev or RPMSG endpoint creation. |
| router/peripheral-qrtr.c/.h | Allocates seven subsystem contexts and queues; opens four sockets each; publishes service4097, looks up CMD, unchecked connect/publish results and short non-HDLC header accesses. Excluded as a unit. Its fixed modem wire layouts are adapted in `a9-router.c`. Other subsystem bases64/128/192/256/320/832 are not published. |
| router/peripheral.c/.h | General broadcast/send/close dispatch to arbitrary backends. Excluded. |
| router/router.c | General command dispatcher indexes packet bytes before checking length and routes host-selected commands to remote firmware. Excluded. In particular there is no NV/EFS diagnostic command route. |
| router/app_cmds.c, common_cmds.c | Local mask/diagnostic command handlers accepting host-selected bodies and lengths. Excluded; no host clients. |
| router/diag_cntl.c/.h | Wire-control formats adapted selectively. Upstream registration walks count-controlled arrays without checking body length; feature handler reads beyond short payloads; DIAG-ID uses unbounded strlen, a native pointer member in a wire struct, and unbounded allocations/response sizes. Entire source excluded; bounded decoder described below replaces the reachable subset. |
| router/masks.c/.h | Mutable per-client mask arrays and size-dependent allocations; excluded. A9 emits fixed ALL_DISABLED empty masks, no client can enable packet logging. This differs from upstream's initial INVALID mask and needs phone validation. |
| router/dm.c/.h | Client management, read/dispatch/broadcast of diagnostic frames. Excluded; no unsolicited firmware payload is printed or written. |
| router/hdlc.c/.h, circ_buf.c/.h | General HDLC encoder/decoder and mutable receive ring. Excluded; A9 only length-validates non-HDLC responses then discards them. |
| router/mbuf.c/.h, watch.c/.h | Dynamic packet queues, flow control, eventfd, AIO io_setup/io_submit/io_getevents/io_destroy, read/writev and timer event loop. Excluded; A9 has four sockets and bounded poll/recv/send, no heap queues or AIO. |
| router/util.c/.h, list.h | General list/string/hexdump helpers; excluded. A9 uses explicit bytewise little-endian accessors and never hex-dumps firmware. |
| tools/send_data.c | Host-selected arbitrary byte command sender via abstract DIAG socket. Never built/staged. |

All upstream pathname-bearing transports and discovery/fallbacks are absent from
the linked executable. There are no storage roots, partition/by-name lookups,
configuration/env-selected paths or fallbacks. The daemon receives only stdio
FD0 null / FD1-2 write-only pipe or null. Native launcher role `diag-router`
verifies the immutable ELF against assets.sha256, enters its own fresh
`noswap,nodev,nosuid,noexec` tmpfs, pivots and detaches its old root, closes all
other descriptors (including high inherited FDs), drops UID/GID/groups/caps,
and applies the exact existing default-deny seccomp filter before exec. Its
postexec constructor rechecks NNP/filter/UID/caps and executable FD closure.
No relaxation of sandbox-policy.h is needed. There is no block or shared-RAM
transport FD in this role; only the rmtfs role receives its reviewed RAM device.

## Bounded transport and handshake

`router/a9-router.c` is a deliberately reduced adaptation of upstream's QRTR
transport and control formats, preserving their BSD notices. It is not a
claim of complete diagnostic-client feature parity. Four explicitly bound
ephemeral AF_QIPCRTR/SOCK_DGRAM sockets publish 4097/0,2,4 and look up 4097/1.
Every socket/bind/publication/lookup/connect result is checked. Readiness is
emitted after publication requests succeed; coordinator must independently
observe actual nameserver rows. No AF_UNIX, AF_INET, RPMSG or UART endpoint is
created. Unexpected remote node numbers, truncated recvfrom/metadata, malformed control
lengths and oversized packets cause a fault and exit. Control's first validated
peer is pinned; subsequent source addresses must match. Nameserver command
peer notifications must be local control-port messages for service4097,
instance1, a nonlocal/nonbroadcast node and a nonzero/noncontrol port. Before
that lookup completes, only CMD is polled so early control datagrams stay
queued instead of racing discovery. A peer change/deletion is a fault, with no
reconnect. The DCI endpoint is drained as well.

The decoder uses fixed 16KiB receive, 256-byte response and 64-ID tables, with
128-byte NUL-terminated process names. It accepts control8 feature negotiation,
control33 DIAG-ID versions1-3, bounded register/deregister arrays and preset
notifications; unrelated controls are consumed without dereferencing bodies.
Feature counts are bounded to64 bytes with up to3 zero padding bytes. DIAG-ID
feature lengths are byte offsets rather than native pointers; complete NUL
termination and response bounds are checked. IDs are2-255 and must be unique.
No registration is stored or used to route any command. Malformed requests or
send failures cannot produce arbitrary path access and close the startup gate
if they terminate the daemon. Socket receive errors fail instead of restarting.

Responses preserve upstream feature/mode/buffering formats, explicitly zero
its two uninitialized feature-response padding bytes, and disable message/log/
event masks with empty bounded payloads. Data/CMD/DCI packets are drained and
discarded; no command request is generated and no raw packet is logged. This
keeps the DIAG sink from becoming an NV-writing service. Modem-side behavior
(including whether this reduced mask policy suffices) remains a runtime gate;
a source audit cannot establish it.

## Readiness, test scope, and limits

Native launcher logs `A9_WIFI_JAILED <role>` only after installing confinement.
Each patched daemon logs `A9_WIFI_READY <role>` only after its fixed FD checks
and successful QRTR service publication. DIAG may later log fixed
`A9_WIFI_DIAG_HANDSHAKE` / `A9_WIFI_DIAG command-peer-connected` messages without
payload/name/ID bytes. Logs require write-only pipe or null endpoints; direct
regular-file redirection is rejected. Readiness does not authorize modem start:
the staged coordinator must verify process survival, QRTR rows, attestation,
and full syscall-trace coverage from before native preparation.

`tests/mainline-wifi/diag.py` checks the source archive pin and applies the patch
before compiling the real parser into ASAN/UBSAN host tests: feature responses,
padding, DIAG-ID versions1/2/3 and reuse, duplicate ID rejection, truncated
registration/overflow, non-HDLC lengths, and 100000 deterministic malformed
packets. Production translation unit is compiled and its undefined symbols
must not contain path/client/device/allocation APIs. The production translation unit also has a checked argument-rejection path
that exits before creating a socket. No test opens a QRTR
socket, executes an ARM64 binary or accesses a phone. The existing actual
native-jail host test exercises the same unchanged filter and new launcher.
These tests establish bounds/confinement behavior, not A9 firmware stability.

Host protection proof has the same limits as round1: it prevents these daemons
from mediating real storage writes. MPSS firmware still runs outside Linux
userspace confinement; PAS/TZ memory firewalls and unsolicited modem firmware
behavior are not proved by seccomp. No qseecomd, RIL, factory/diagnostic NV
client, bootctl, persist mount, or other stock modem broker is added.

The pinned QRTR `src/lookup.c` is also built/staged as static `qrtr-lookup` for
coordinator-only nameserver evidence. It has no postexec constructor and is not
a radio daemon or asset-manifest role: the coordinator invokes it with fixed
service4097 and a deadline, validates completion and three rows against the
logged runtime local node. It only opens an AF_QIPCRTR datagram socket, queries
the local control port and prints decoded row fields. It does not open paths,
read calibration, create a host diagnostic client or issue DIAG commands. Its
pin/archive/source/binary hashes are included in the same build manifest.

Review23 finding5 is addressed for rmtfs by a latched first-fault flag. Every
QMI error, response-encode/send error, backend/shared-RAM I/O or sync error,
unknown/malformed/oversized message, unexpected peer, QRTR reset or recv/select
failure emits `A9_WIFI_FAULT rmtfs ...` and ends its event loop with failure.
No ENETRESET retry or silent continuation remains in the A9 path. Unrelated
validated local nameserver client-close notifications are ignored; loss of the
pinned modem client is fatal. The controller must stop MPSS on the marker or
daemon death; the daemon itself has no remoteproc privileges. Additional actual
event-loop tests inject wire/path/send/I/O/sync/sender/reset/short-RAM failures
and assert precisely one receive/select before failure, using only fake QRTR
and mock RAM/backend functions under ASAN/UBSAN. Production backend bounds and
confinement remain covered separately; these mocks are not a hardware test.
