# E Ink quality diagnostics and offline fixture

2026-10-17. Offline software validation; no optical or electrical qualification.
The implementation leaves the E Ink power path and default modes unchanged.

Compat accepts `--diagnostics N`, with decimal N in 0..4096 (default 0).
It emits at most N `a9-eink-diag` stderr lines per process, shared by the socket
and property threads. Fields are monotonic milliseconds, fixed event name,
numeric sequence/channel, numeric value and `accepted`. For `event=mode`,
sequence counts socket mode requests; `accepted=0` means deferred, and a later
successful retry emits `accepted=1` with that same sequence. A newer request
supersedes the pending one. Repeated failed retries do not emit diagnostic
lines. `accepted=1` means a successful sysfs write, not a completed waveform.
No app, package, process, socket, account, device or network identifiers are
included. Existing error logging is separate from this bounded numeric stream.

`event=lamp` is emitted only after a successful changed channel write. The
sequence field is a fixed channel index: 0 warm center, 1 white center,
2 warm edge, 3 white edge. Value is the scaled numeric LED output. Unrelated
property wakes and unchanged values produce no diagnostic record. This measures
software write completion, not LED current or shared-supply behavior.

The kernel module parameter `a9_eink.input_diagnostics` is root-only, false by
default. The connector's read-only `eink/input_stats` exposes fixed-size numeric
aggregates; it contains no pixels or identifiers. Reservations cap input attempts
and profiled atomic commits at 4096 each per device lifetime. Turning diagnostics
off and on does not reset budgets. In-flight samples finish if disabled. Mode
and manual-clear accepted-request counters each stop at 4096; the corresponding
last accepted request time/mode stops with the counter. No event log is stored.
These counters describe requests, including repeated/coalesced ones; `status`
continues to describe the worker's last job.

Input samples time four separate stages in nanoseconds: framebuffer begin CPU
access, shadow mutex wait, conversion/damage iteration, and end CPU access.
Each stage has total/max; count gives the denominator. A failed begin CPU access
increments `failures`, consumes an attempt, and contributes no successful sample
or timing totals. `pixels` sums actually visited clipped rectangles, including
overlaps. `full` means at least one clip covers the complete portrait canvas;
other successful samples are `partial` (including empty damage). Multiple partial
clips are not relabelled full. `unchanged` means conversion found no luma changes,
not that a conditioned target or an optical frame was identical.

The existing DRM phase profiler separately counts fence-wait total/max on the
virtual input device's atomic commits. Per-commit storage survives asynchronous
commit cleanup; the normal DRM helper sequence and fence/coherency calls remain.
Fence samples include commits with no active plane and are **not** the same
population as successful conversions. Mapping/import, GPU rendering and the
rest of the atomic commit are outside these conversion measurements. Timers,
bookkeeping and mutexes add overhead while opted in. No timer or diagnostics
were added to waveform drive or power-off paths, including emergency cutoff.

The kernel patch is 0047; the mirrored driver and packaged patch must stay in
sync. Tests are described in
[the fixture README](../tests/mainline-eink-quality/README.md).
The fixture uses synthetic greys and antialiased glyphs and checks full targets,
requests, step arrays, packed history, complete frames, final neutral and
committed history against the existing stock oracle. The input-conversion test
separately covers RGB/BGR order, pitch, clipping, rotation and changed bounds.
Only code and synthetic fixture definitions belong in git; composer/table data,
photos, personal frames and logs do not.

## Default mode decision

Keep Smooth/518 as the boot and app fallback for this change. Clear/515 produces
16-level targets and avoids Smooth/Balanced's binary texture for UI grey235/230.
It is a sensible persistent quality reference for the muddy-screen report.
The synthetic fixture does not prove it fixes physical bands, residual history
or weak optical density. At bin 10 its ordinary waveform is 30 transport frames
versus Smooth's 8: approximately 353 versus 94 ms at the measured 85 Hz transport
rate, before input/preparation/startup costs. Manual Clear is 60 versus 48 frames.
A global switch would therefore impose a substantial latency tradeoff without
a matched optical comparison.

Compat has no startup/default-mode write; the kernel owns its Smooth default.
The app's `RefreshModeManager.kt` reads `refresh_setting` with fallback `"2"`
(Smooth), and per-app misses use that default. Merely making compat or the kernel
select Clear would be overwritten by later app focus/default restoration.
A justified future mainline-only policy must coordinate the app's default and
per-app fallback with boot behavior and respect existing saved choices. No app,
compat startup policy or kernel mode default is changed here.

The 32x32 source-contiguous luma/transpose candidate remains in the host test
only. It matches the reference bytes and damage bounds, but host benchmarking
shows no consistent speed benefit. Production `a9e_fb_to_grey` is unchanged.
Host cached-memory times cannot establish the phone's WC conversion bottleneck;
the opt-in diagnostics provide a way to measure it in a separately authorized
future run. Fences, cache attributes and allocator behavior are unchanged.

## 5. Clear shadow curve and stock-like policy options (2026-10-17, offline)

Status: implemented and host-tested; **never run on the phone**. No waveform,
HV, PG, VCOM or rail code is touched (candidates 0048/0049 are not involved).

**Clear UI shadow curve (kernel patch 0050).** Spec: `runs/eink-stock-policy.md`
"Weak black in Clear". `a9t_clear_ui_grey(x)` = 0 for x<=30, `(x-30)*200/170` for
30<x<200, x otherwise; Clear's G16 table becomes `g16[clear_ui_grey(x)]`
(`g16s`). It applies **only** to mode low byte 3 (515, with or without the
bit-8 diffusion); every other low byte (incl. 514/520) is byte-identical (the
test scans all 256 low bytes). Core default is OFF (`a9t_init`, so all older
host suites and the oracle fixture reproduce today's bytes). Driver: module
parameter `clear_shadow` (default **1**, the mainline test image), sysfs
`eink/clear_shadow` (0/1), a change queues the same full conditioned redraw as
B/W/C. Compat: `shadow0|shadow1` socket commands, prop
`ro.vendor.a9ml.eink.clear_shadow=0|1` at start (writes give up after 20 tries
on an older kernel). Proven: luma 31/33 -> level 0 (stock path: 1/2), 0..43 -> 0,
bytes 200..255 unchanged (LUT inputs; final TARGET levels are preserved exactly only for plain 0x203 Clear: with bit 8 (0x303, 16-level diffusion) error from darker pixels can change light-grey levels, measured 11,635 pixels in a mixed probe, e.g. 13 -> 14), all 16 levels still reachable, composition with every
C=0..100, uniform fields 31/33/235 -> 0/0/14, `tests/mainline-eink-quality/shadow.c`
(run by `run.sh`, no oracle needed). Optical validation is still open: it
darkens some content below 200 (icons, disabled text, photos).

**Dynamic refresh (compat, `eink-policy.h`).** Off by default
(`ro.vendor.a9ml.eink.dyn_refresh=1`). Rest = the chosen mode (`default_mode`,
with `lock_mode` for Clear; later explicit `b/c/s/p` requests replace it; without
a default it assumes the kernel's 518). After N (`dyn_events`, 4) `sv` events within
`dyn_window_ms` (500) it writes `dyn_fast` (b = 513, or s = 518) once; further
events only push the deadline (coalesced); `dyn_restore_ms` (1000) after the last
event it writes the rest mode once. Like stock it leaves only Clear unless
`dyn_any_rest=1`. An explicit mode request while fast becomes the rest mode and
cancels the restore, so it is never overwritten (with `lock_mode` app requests are
ignored as before). **Signal needed (not present today):** one socket line `sv\n`
per vertical scroll event on `a9_eink_socket`; the natural source is a9service's
AccessibilityService `TYPE_VIEW_SCROLLED` with `scrollDeltaY != 0` (API 28),
possibly rate-limited by its notification timeout, which is why N and the window
are properties. Not added to a9service (daily ROM) here.

**Activity-change clear.** Off by default (`activity_clear=1`). Signal: `ac\n`
per activity/window change (accessibility `TYPE_WINDOW_STATE_CHANGED`). A burst
coalesces into one clear `activity_delay_ms` (300) after the last event (capped at
4 x delay after the first); it fires only if the re-arm timer (`activity_rearm_ms`,
6000, stock) has expired, otherwise it is dropped, as stock skips rather than
queues. Explicit `r` stays immediate and bypasses the timer.

Tests: `tests/mainline-eink-quality/run.sh` (shadow), `scripts/mainline/eink-compat`
`make policy-test test test-sanitize` (fake clock logic + 6 socket cases).

**Fix round (review 68).** Policies stay OFF by default. Hard floors in
`eink-policy.h`, independent of properties: activity re-arm >= 6000 ms, delay >= 100 ms,
dyn restore >= 100 ms. Activity events inside the cooldown are dropped on arrival.
A global limiter in the daemon starts at daemon start and is restarted by every
clear it writes (automatic or explicit `r`); an automatic clear needs 6 s since the
last one (explicit clears are never blocked), so a restart or any configuration
cannot produce a clear storm (rail fault after full clears: runs/eink-quality-1.md
section 5; automatic clears remain unqualified electrically). Dynamic refresh now
needs N events inside ONE window that starts at its first event.
