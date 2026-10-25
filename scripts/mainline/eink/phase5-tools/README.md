Current N-e runtime tools. No image/module rebuild is needed. The user runs these
from an existing root console; preparing these files performs no phone operation.

Place the contents of `device-tools.tar.gz` together in
`/data/local/tmp/phase5-tools/`. Keep SELinux and the existing E Ink protections as
configured. A missing node/event or denied operation is an error to report; the
tools do not change policy, device nodes, panel gates, priorities, affinity,
clock votes or audio state.

For a baseline, run this in the phone root console and use ordinary UI swipes.
It stops after the first recorded phase5 >=30 ms, or600 seconds. No video is
required. Use a new output path for each attempt.

```sh
cd /data/local/tmp/phase5-tools
chmod 700 latency-hold phase5-watch
sh ./phase5-capture.sh /data/local/tmp/phase5-baseline-01 600 30000
```

For the zero-latency comparison, start this in root terminal A:

```sh
cd /data/local/tmp/phase5-tools
./latency-hold 600
```

Wait for `READY ... requested_us=0 effective_us=0`. In root terminal B:

```sh
cd /data/local/tmp/phase5-tools
sh ./phase5-capture.sh /data/local/tmp/phase5-qos0-01 600 30000
```

Use the same ordinary UI activity. When capture ends, press Ctrl-C in terminal A
to release the request immediately; `RELEASED` confirms the helper closed its
own FD. Otherwise it expires automatically after600 seconds (range1..900).
SIGINT/TERM/HUP close the request; the kernel also removes it if the process is
killed. To stop it from another console, use `kill -TERM PID` with the exact PID
printed by this helper. No shared sysfs value needs restoration. The zero limit
constrains CPU idle exit latency globally and can increase idle power. It does
not hold a suspend wakelock or request a CPU frequency. This is a comparison
experiment, not a proven diagnosis or an exact replay of stock's configured vote.
You can also run the latency helper alone with normal UI use to avoid trace
instrumentation as a confounder.

Capture details:

- The fresh `a9p5_PID` trace instance has512 KiB per CPU; the recorder retains at
  most8 MiB in RAM and then writes private output. It identifies the sole
  `a9_eink` consumer, filters the MSM phase markers to it, and requires the
  post-kickoff `msm_atomic_flush_commit` before opening phase5. The earlier
  phase3 flush wait cannot trigger. No starvation_off event is required.
- The threshold uses mono **trace timestamps**, not reader wall time. The first
  event timestamp >=30 ms while phase5 is open provisionally triggers. It keeps
  recording through wait completion or100 ms after observing that crossing. A
  completed wait <30 ms cancels an out-of-order cross-CPU provisional trigger.
  A completed >=30 ms wait is definitive given intact markers. `phase5_end_ns=0`
  is an open/ambiguous capture, not proof of a hardware hang. With no intervening
  events the crossing is recognized when an event arrives, not at an exact
  30 ms timer deadline. `trigger.json` records the basis and duration.
- Probes record entry/return of `dpu_hw_ctl_get_flush_register`, its actual
  existing `value=$retval:x32`, the video vsync callback entry/return, and the
  commit-done wait entry/return. No additional MMIO reads are made. IRQ-context
  flush reads are also retained and must be separated from the waiter's samples.
  The DPU pending flush mask tracepoint is included if available.
- Waiter sched_switch, waking/wakeup/migration, CPU idle, IRQ/softirq events,
  interrupt counts and RT runtime settings are retained. The saved event formats
  define fields. Probe names include the unique group prefix so `kprobe_profile`
  can identify hit/missed counts even though that interface omits group names.
- Cleanup disables/removes only this instance and its own probes, including on
  errors/signals. It refuses an existing output, trace instance, probe group or
  multiple waiters. Errors are nonzero and leave useful stderr.
- Check `buffer-stats.txt` overruns/dropped entries, `kprobe-profile.txt` nmissed,
  and `trigger.json` recorder_overwritten_bytes before interpreting missing
  events. Oldest recorder history can be deliberately overwritten. The trigger
  may be from a neutral commit too; correlate the read-only E Ink status/snapshot.
- Trace/probe execution and the reader perturb timing and idle behavior. The
  reader waits up to1 s outside an observed phase5 and checks at5 ms while it is
  open, bypassing trace-pipe wakeup watermarks. Compare equally instrumented runs;
  lack of a late frame is not evidence that QoS fixed it.
- RAM-backed `/data` output is lost on reboot. The user should preserve the new
  directory first; it includes private trace/dmesg/setup data. No private phone
  evidence was read to prepare these tools.

On the host, after the user has preserved the capture:

```sh
python3 analyze-trace.py /path/to/capture/trace-private.txt > phase5-timing.json
```

This reports phase5 duration, non-IRQ waiter poll-return spacing quantiles and
largest gaps with actual sampled flush values, vsync times, and numeric scheduler
and idle events. A zero first observed after a long sleep cannot date when the
hardware cleared; a returned nonzero sample after wake does establish it was
still pending at that read. A wakeup-to-next-switch-in gap separates runnable
scheduler delay from an outstanding sleep/timer. CPU idle exit near that interval
is correlation; IRQ delivery, timer expiry and scheduler delay still need to be
compared. The full private trace remains the authority.

Host reproduction (source tree):

```sh
python3 build-host.py /home/user/A9-build/mainline/phase5-tools-20261021
python3 test-tools.py /home/user/A9-build/mainline/phase5-tools-20261021
python3 test-capture.py /home/user/A9-build/mainline/phase5-tools-20261021
```

Builds are native plus static AArch64 musl. Tests use real production sources,
QEMU, fixture trace/FIFO input, link-wrapped QoS I/O and a fake tracefs lifecycle;
there is no host device access or phone qualification.
