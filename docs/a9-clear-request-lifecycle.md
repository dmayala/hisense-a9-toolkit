# A9 clear-request lifecycle

2026-10-12. This note describes the bounded, reviewed software path for an
`epd_force_clear` request. It explains how a request can reach the composer’s
clear latch and a later producer decision. It does not promise that pixels or
ghosting are physically cleared.

## What a successful request means

The kernel `epd_force_clear` sysfs store parses a base-10 integer. A successfully
parsed value of `1` calls `kobject_uevent_env` with `KOBJ_CHANGE` and the
`EPD_CLEAR=1` environment entry. The handler ignores the uevent call’s return
value, stores the parsed value for its readback path, and returns the write
count. So a successful write of `1` establishes successful parsing and a call to
the event helper; it does not establish event delivery, userspace receipt,
composer admission, a completed software batch, or physical clearing. Other
successfully parsed values are stored but do not request a clear event. Parse
errors return an error and do not store the value.

On the composer's inspected EPD event branch, the path comparison is
case-insensitive; the `EPD_CLEAR=` payload search is case-sensitive and parses
the first matching entry. `EPD_COMMIT=1` is checked first and takes dispatch
precedence. A matching `EPD_CLEAR=1` dispatches `ClearGhosting` on the display
object held by the session. The runtime identity of that display object is unresolved.

## Latch, wake, and later work

`ClearGhosting` first checks two object-state guards. If either guard fails, it
returns silently without changing the existing latch. When both pass, it stores
`1` in a boolean latch. Repeated accepted calls coalesce absent intervening
consumption; the inspected path has no request counter.

| State when accepted call stores the latch | Immediate effect | How the handler can reach a later decision |
|---|---|---|
| Idle, no wake already pending | Set latch; mark wake pending; broadcast the condition variable. | The waiting handler can wake, subject to its pause, bitmap, and black-image guards. |
| Idle, wake already pending | Set latch; no additional broadcast. | The pending wake or image can lead to a decision, subject to the same guards. |
| Busy, including a batch in progress | Set latch; do not broadcast. The current batch does not reread the latch. | A later decision may occur without another wake if an image is pending; otherwise the handler needs a later wake. The guards can still defer it. |

Observed in the instructions: early latch reads participate in request
construction. On paths that reach it, a later latch snapshot can set a marker;
the unchanged-MODE, latch-zero route skips that snapshot. Later mode/log reads
can observe the live latch without rebuilding selected steps. At admission, the handler marks itself
busy and resets the latch only when the marker is nonzero. A zero snapshot
preserves the old marker; it does not initialize it.

Inferred consequences, conditional on the inspected order: a store after a zero
snapshot is not cleared by that admission if the prior marker was zero and no
other marker writer intervenes. A store after a nonzero snapshot can be absorbed
into marked admission without creating another request. An arrival between early
request reads and a snapshot actually reached can set the marker without
rebuilding steps. These are inferences about software behavior; they have not
been observed on a device.

There is no delay bound. A pending image can let the handler reach a decision
without a new wake. With no pending image, a wake is needed, and a wake can still
be stopped by pause, bitmap, or black-image checks. A logged clear request or
write success therefore says nothing about when, or whether, a later decision
or batch occurs.

## Mode and image are separate checks

An unchanged MODE comparison checks the prior and current mode bytes. It does
not compare image buffers. Under stable relevant latch reads, unchanged mode
with latch zero gives request kind 0; unchanged mode with latch set gives kind 2,
which bypasses duplicate-image suppression. Some unchanged modes construct no
clear steps; this note does not infer their downstream outcome.

The inspected duplicate IMAGE suppression has a separate, exact predicate:
the update flag is set, request kind is 0, and the current and previous image
buffers compare equal. A kind 1 mode transition and a kind 2 clear request
bypass that check, including when the image is identical. Conversely, an
unchanged mode alone neither proves an identical image nor suppresses work.

Some transitions also set the same latch: prior mode 6 or 9 to current mode 1
or 3. This is an inspected request-construction path, not a separate hardware
clear signal. Its consumption and reset still depend on reaching the marked
admission path.

## Evidence and limits

The code observations above summarize frozen report revision 5 and the final
independent PASS review. That receipt supersedes the report's historical
in-progress status; it accepts only the bounded central findings.

- Composer identity: 2,049,912 bytes, SHA256
  `8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
- Frozen report SHA256:
  `2b177321004b99ed7c3736b424d21b0ebc7627e760f2d73ce6d27d9994b1babd`.
- Frozen report manifest SHA256:
  `5cb79c82a84fe8e861a551e00c4332085a3505c39e6ec5ed719647aca699306a`.
- Independent review SHA256:
  `caddff36518144493e0f3985e8b0a48c6eef11331142017b4669401c5064059d`.
- Review checked 22 selected original ARM64 cases, imports, relocated vtable
  slots and a saved kernel handler. Kernel facts apply to the documented Image
  SHA256 `2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`;
  its identity as the running kernel was not established.

The event delivery path and kernel kobject/path match, runtime display object,
initial latch value, complete writers/aliases, outer caller serialization, and
whole-process concurrency remain unknown. The review did not establish how often
guards fail, when a later wake occurs, or whether a physical display clears
ghosting. It gives no FPS, timing, interval, or firmware/app patch recommendation.

For the adjacent request-construction and count contracts, see the
[composer request model](a9-composer-requests.md) and the separate
[gate-zero count model](a9-composer-startup.md). The latter leaves the request
model’s original gate-zero behavior unchanged.
