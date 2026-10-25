# FIX3: negotiated ring size and PREPARE recovery

Coordinator acceptance on 2026-10-22: speaker ringtone heard "clear as day";
start clean, end pop. The subsequent standby correction and earpiece/headset
check are described in [STOP-AND-JACK.md](STOP-AND-JACK.md).

The FIX2 live capture at 20261022T163809Z shows successful ASM open, ADM matrix
mapping, RUN acknowledgment, and two WRITE_DONE callbacks. Each queued buffer
contains1440 stereo S16 frames (5760 bytes). The pointer2879 is two completed
periods minus the driver's deliberate one-frame offset. It does not establish
that the codec or speaker received audible samples, but rules out the claim
that the DSP never processed the submitted buffers.

The first XRUN is explained by the captured parameters: hardware buffer5760,
appl_ptr4096 and hw_ptr2879 give availability4543. ALSA stops when availability
reaches stop_threshold4096, despite1217 frames still pending. The shipped
tinyalsa setter copies a1024x4 caller configuration, negotiates1440x4 into its
internal copy, then uses the original pointer for buffer accounting and default
software thresholds. Thus it reports4096 rather than5760 and starts/stops at
2048/4096 rather than2880/5760.

The builder requires donor SHA256
`bc39aebfb55f510c3b34f3a223106cc5e1d85c95e1265ba29f38471fc5edce87`.
At file0xa698 (Ghidra analysis0x10a698), it replaces `mov x20,x1` with `nop`.
The preceding instructions already copied all64 config bytes into pcm->config;
x20 already points there. Keeping that pointer lets negotiation, buffer sizing
and SW setup use the same object. This is a one-instruction donor binary fix,
not a rebuilt library. The exact hash and old instruction are checked; ABI,
file layout, caller config and explicit thresholds remain unchanged.

Recovery has a second bug. ALSA PREPARE resets its application position, while
q6asm-dai retains queue_ptr2880. Rewriting4096 frames then queues no complete
period: floor((4096-2880)/1440)=0. The trace accordingly shows RUN/STOP cycles
with no intervening WRITE calls. In addition to resetting queue_ptr during
prepare, the patch resets Q6ASM's DSP buffer index and hardware-period index
when allocating a fresh mapping. This aligns all three cursors at zero and
avoids sending data from the old buffer index after recovery. Existing mappings
and pause/resume are unaffected. No exported ABI changes.

Stock HAL REA confirms device selection precedes its pcm_open/pcm_prepare
helper (analysis0x14a5f0). Mainline's first open/route/RUN already succeeds.
The positive matrix_map returns248/249 are remaining wait_event_timeout jiffies
after a successful response, not DSP error codes. AFE port0xb032 and SoundWire
prepare/start return0. Neither the evidence nor this fix calls for a new port,
clock, calibration or amp sequence. Stock's older HAL/kernel stack does not
establish correctness of the donor tinyalsa_new setter or mainline cursors.

`build-fix3.py` replaces only libtinyalsav2.so, q6asm.ko and q6asm-dai.ko over
FIX2, preserving file metadata and verifying readback/fsck. The existing boot
is reused. The existing Android fixture has five real-donor configuration
cases: rounded period, exact period, two-period ring, explicit thresholds and
HW_PARAMS failure. It reproduces the original accounting and verifies the
patched library, then retains the five route/first-error regression groups.
Kernel compilation checks imports/exports and vermagic. Hardware behavior and
audibility remain untested by the worker.

The supplied capture has no DAPM/register/GPIO readback and no mixer values
despite dump rc0. Those omissions cannot prove amp power or analog correctness.
The same host audio-live.sh remains available for the coordinator's next
attended capture, with --image-dir selecting the output folder. No worker
phone/playback operation is part of this delivery.
