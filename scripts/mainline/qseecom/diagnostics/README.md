# Independent kernel-status to logcat bridge

The [PIN26 follow-up](../../../../docs/mainline-fbe-pin26.md) adds
`A9_RPMB_REQUEST_TRACE` and `A9_RPMB_WRITE_TRACE`: bounded public frame-control
addresses, map/partition classification and whitelisted operation context.
No payload, key ID, MAC, nonce or counter is emitted. These diagnostic schemas
do not grant writes. Refresh this daemon along with the kernel for integ27.

`a9-rpmb-diag` is a static Android arm64 daemon packaged by
`credentials/prepare-vendor.py`. A dedicated enforcing init service starts on
`init` and is explicitly requested before existing `vendor.qseecomd` startup.
It has its own read-only, nonblocking `/dev/kmsg` cursor. It does not depend on
the early boot logger, debugfs mounts, a counter reader or a helper thread.

`schema.h` is the complete allowlist. The production C parser requires the full
kmsg header and one complete printable message. It accepts the existing numeric
REGISTER, SCM, GUARD, REJECT, RESULT, KMTB_READ/WRITE and QSEE_LOG/CAPTURE schemas, and maps the exact QSEE
transport-ready message into `A9_QSEE_INIT ready=1 whitelist=[01]`. Any appended
field, pointer, sector, TA name/content, embedded NUL, newline or unrecognized
record is rejected. SCM function IDs are enumerated public call IDs; they are
never buffer addresses. Secure status is signed64 because the kernel prints
`long`; other return codes are signed32. The bridge does not mirror its own
heartbeat records and never writes to kmsg.

`A9_RPMB_REJECT` classifies a refusal's opcode/direction, frame count, length
match and known first-frame request type; it contains no addresses or payload.
`A9_RPMB_RESULT` reports the stock read/write listener operation status, which
is distinct from its QSEE reply status. See [INTEG23 diagnosis and fields](../../../../docs/mainline-fbe-rr6.md).
`A9_RPMB_MAP phase=[12] valid=[01]` reports invalidation and reply processing
without exposing table contents or extents. GUARD mode2 adds operations5
(scoped GK or single-entry KMTB data write) and6 (nonmutating result request); mode1 remains accepted
for historical logs and unchanged bypass refusals.

`A9_KMTB_WRITE` records every classifiable KMTB candidate/submission/result
with phase, permission, add/remove classification, reason and changed-slot count,
never IDs or addresses. `A9_KMTB_READ` reports before-image availability.
See [the per-entry policy](../../../../docs/mainline-fbe-kmtb-guard.md).

Queue RAM is bounded to64 records of256 bytes, with an8192-byte read buffer.
The cursor drains at most64 reads per iteration; errors and catch-up batches
back off500ms. EOF/unexpected errors retain the cursor and back off rather than
reopening/replaying or spinning. Kernel overruns, oversized records, queue drops
and read errors are counted. A full queue drops new records, retaining order of
queued records. Missing status is not success.

The public liblog write API returns1 after a void logger callback and cannot
report a failed logd write. Per coordinator instruction, this bridge instead
uses the checked Android logdw datagram layout: packed11-byte header
`uint8 id, uint16 tid, uint32 seconds, uint32 nanoseconds`, then WARN priority5,
`A9_RPMB_DIAG` plus NUL, and sanitized message plus NUL. Buffer ID0 selects main.
The local Android source reference is
`/home/user/A9-build/ref/lineage-system-logging-e6332164/liblog/`:
`include/private/android_logger.h:49`, `include/log/log_time.h`,
`README.protocol.md`, `logd_writer.cpp:120`, and `logger_write.cpp:385`.

Only a successful full datagram `sendmsg(MSG_DONTWAIT|MSG_NOSIGNAL)` dequeues a
record. Missing logd, backpressure and interrupted/failed sends retain the head
for a later retry. Other socket errors close the connection for a later
reconnect. Each accepted queue record is submitted once during this process's
lifetime. Socket acceptance is not a logd persistence/reader-delivery
acknowledgement; crash/reboot replay and logd retention are not covered.

A separate heartbeat is submitted at startup and every5seconds when delivery is
possible. Failed heartbeat delivery retries on the normal500ms cadence. It is
not stored in the bounded record queue:

```
A9_RPMB_DIAG startup=1 seq=0 overrun=0 truncated=0 submitted=0 queued=0 dropped=0 readerr=0
```

Counters saturate at uint32max. `submitted` counts sanitized records accepted by
the logdw socket; `seq` counts successful previous heartbeat submissions. Zero
records is not proof that an RPMB operation succeeded. No credential, RPMB,
QSEE, SMC, ioctl, mount or persistent-counter operations exist in this daemon.
Its enforcing CIL grants only process startup, SYSLOG/read-only kmsg and the
logdw datagram route; it grants no QSEE/RPMB device access and no kmsg write.

Build/test on the host:

```
python3 -I scripts/mainline/qseecom/diagnostics/build.py --out /local/build
python3 -I scripts/mainline/qseecom/diagnostics/test-host.py --out /local/tests
```

Four focused C/ARM64 groups exercise the production parser, bounded queue,
late/backpressured UNIX fake-logd transport and production-loop error backoff.
The parser group also compares coordinator Python extractor fixtures. The fifth
group is actual vendor preparation: policy compilation, init verification,
readback/labels, input pins, filesystem consistency and no image growth.
Only source goes into git. Tests use synthetic kmsg records and a local UNIX
socket under ARM64 QEMU, with no phone access. Runtime service/SELinux/logd
availability remains a coordinator-owned device check.

The cached-PTBL bootstrap also emits `A9_RPMB_BOOTSTRAP phase=1..6 rc=<signed>`.
Phase5 is final map publication/parser status; phase4 is the optional complete
RR before-image read. Rebuild this daemon with the kernel change so the closed
schema retains these records in logcat. See the read-only bootstrap follow-up
in `docs/mainline-fbe-pin26.md` for the request sequence and trust limits.


The passive qseecomd open observer also classifies user0 fingerprint FTS files
under `/data/vendor_de/0/fpdata` as `A9_SFS_OPEN root=3` (persist1, tzstorage2).
No filename, template ID or payload is emitted. The existing credentials build
refreshes `liba9-attest-fs.so`; the numeric credential summary preserves root3
errno/counts. This is direct logcat output, not new kernel-bridge input. See
[the selected FTS backend trace](../../../../docs/mainline-fp-enroll.md).
