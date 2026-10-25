# A9 Android audio output candidate

Speaker playback, earpiece and both WCD headphone channels are attended-confirmed
with no stop pops after [FIX4](FIX4.md). [FIX5](FIX5.md) fixes the failed host
launcher and starts WCD jack detection, which was never reached by the old
machine callback. Jack in/out and the revised launcher await attended use.

FIX3 (`plain-integ-audiofix3-20261022`) follows the live FIX2 trace: two DSP
WRITE_DONE callbacks precede an XRUN because negotiated ring5760 has software
stop threshold4096. The donor tinyalsa setter updates its internal period
size, then computes buffer accounting from the original caller config.
`build-fix3.py` applies a hash-pinned one-instruction correction to use the
internal negotiated config. It also rebuilds Q6ASM/DAI vendor modules with
fresh mapping/prepare cursors, preventing recovery RUNs without fresh writes.
The plain-integ boot, FIX2 routes/gain and diagnostic adapter are retained.
Existing Android fixture cases reproduce the original bug and check the
patched donor library. Listening and downstream analog output still need the
coordinator's attended test. See [FIX3 details](FIX3.md).

The latest host candidate is `plain-integ-audiofix2-20261022`: diagnostic vendor
plus stock Music selection before PCM, five amp write attempts at 2 ms, and
stock QRD RX2 gain75. Build it with `output/build-fix2.py`; use the unchanged
plain-integ boot. The START dependency and gain ramp described in the older
pop candidate are removed. [Capture instructions](LIVE-DIAGNOSTICS.md) cover
the same host script for both diagnostic and FIX2 images. Playback is held
while the user is AFK; audible success remains untested.

Host-built for the existing mainline build-m boot, using the camera-preview
vendor (`8d924f61156a08141b249223ac22a78c742786d1e62d4c6d00d435a8ce4b5d4a`).
The coordinator confirmed direct speaker sound on r5. R6 earpiece sound,
Bluetooth playback and disconnect recovery still require the coordinator's
attended tests. Framework speaker playback was subsequently confirmed on
integrated N/n-a. See [speaker pop sequencing and E Ink coordination](POPS.md)
for the next candidate. No device command is run by this builder.

`build.py` creates `vendor-overlay/` and an ext4 `vendor.img` without rebuilding the kernel Image, mounts, root or device tools. R6 rebuilds
the machine-driver module against the existing kernel ABI; the pop follow-up
builds the machine/amplifier pair together. It keeps at least 8 GiB free plus a
512 MiB build allowance. Retained output is about 0.9 GiB. Images remain private
because the base vendor contains private modem data.

Volume ranges and speaker media steps: [configuration and integration](VOLUME.md).

The [default-off LPASS clock-hold candidate and morning comparison](STALL.md)
incorporates the later n-c continuous-playback phase5 stall evidence.

## Implementation

* `post-fs-data` loads the existing ordered `modules.audio-poc` list through
  `a9ml-audio.sh load`, waits for the exact card name and playback node, and
  reports `vendor.a9.audio.ready=1` / `vendor.a9.audio.state=ready`. A late sensor
  ready notification retries a failed loader. ADSP lifetime remains with the
  existing sensor owner. Loading selects no playback route.
* The original AOSP AIDL core service is copied from the audio APEX to a vendor
  executable, with its exact dependencies in `/vendor/lib64/a9-audio`. Its init
  entry uses the existing `vendor.audio-hal-aidl` name and is parsed before the
  APEX entry. Android init ignores the later duplicate; the donor APEX entry has
  no `override`. Original effect service and APEX manifests remain installed.
  See [AOSP init parser](https://android.googlesource.com/platform/system/core/+/android16-release/init/service_parser.cpp).
* Only this core service preloads `liba9audioroute.so`. It wraps tinyalsa
  `pcm_open`, `pcm_close`, and parameter queries. Mixer routing precedes PCM
  opening: DPCM rejects an unrouted MM1 open. Successful close removes the
  playback route; failed setup attempts to disconnect every endpoint. It uses
  the existing identity/range/enum/readback checked ALSA control implementation.
  `mixer_paths_a9.xml` generates the adapter's control arrays at build time.
* The same private preload implements the ashmem allocation API with native
  sealed memfds for this core process. The donor libcutils requires an old
  ashmem-ioctl compatibility probe before using memfd; without `/dev/ashmem`
  its command/reply/data FMQs cannot allocate on mainline. The adapter uses
  size seals, preserves shared mmap and write-protection semantics, and
  delegates imported legacy descriptors to the donor implementation. It
  changes no system-wide property or library. Build-m already carries a legacy
  ashmem driver (`CONFIG_ASHMEM=y`), so the missing device in the host replay
  does not establish a phone failure. The revision3 phone capture identified
  invalid SCO addresses as the first APM failure instead. The memfd bypass is
  retained unchanged; its necessity on the phone is unconfirmed.
* Speaker uses actual card0/PCM0, AUX/WCD RX1 and the existing DAPM AW87519 amp.
  Earpiece policy address `CARD_0_DEV_24` is an adapter alias to that same MM1
  PCM, selecting WCD RX0/EAR. PCM24 is **not** a kernel device. Do not point
  generic playback tools at PCM24. Output opens are exclusive; a conflicting
  open is rejected. The HAL tears down/reopens when its ALSA address changes.
* MM2/card0/PCM1 capture selects the already proven main-mic path automatically
  and preserves active playback. Digital mic gain is index84 (0 dB). Playback
  macro gain is index54 (-30 dB), fixed for this initial candidate. Android's
  software stream volume still applies.
* Primary explicitly owns attached Speaker, Earpiece and Built-In Mic, each
  with a 48 kHz PCM16 profile and a mix route. Speaker is the default. The
  Bluetooth module owns A2DP/LE devices, r_submix owns remote-submix devices;
  no device type is declared in multiple modules and no device is orphaned.
  The BLE broadcast port/mix/route is removed: it uses an enum outside the
  official policy 7.0 schema, while its profile is disabled in this build.
* Preserve the existing `IBluetoothAudioProviderFactory/default` version5
  declaration in the audio APEX's `etc/vintf/android.hardware.bluetooth.audio.xml`.
  The donor core service creates this provider inside ModuleBluetooth. Do not
  duplicate its declaration in vendor/etc/vintf: that prevents the entire
  device manifest from loading and stalls Android boot. Align Bluetooth with
  this provider using `persist.bluetooth.system_audio_hal.enabled=false` and
  `persist.bluetooth.bluetooth_audio_hal.disabled=false`. Software A2DP and
  LE offload-disabled properties remain set; no DSP/offload path is added.
  LE unicast policy entries remain, but the Bluetooth feature profiles are
  still disabled. LE and SCO/cellular-call audio are not qualified by this work.

The GSI defaults to its `sysbta` provider, which is a different instance and
process from the provider/session state used by the vendor AIDL Bluetooth
module. Disabling that redirection and using the existing default factory addresses
that mismatch. Revision1 mistakenly duplicated the existing APEX factory
declaration; the coordinator observed VINTF rejection and stalled boot. Revision2
removes the vendor duplicate and checks the actual packaged manifests across
vendor and every vendor APEX. Static analysis of the shipped GSI policy library also matches
the supplied crash offsets: 0x896a0 calls this function for type0x20 (SCO
headset); 0x8986c calls it for type0x10 (SCO). With neither type owned, these
recurse into each other before a later carkit fallback can return. Primary now
declares all three SCO outputs plus SCO input with separate 8/16 kHz profiles
and routes. Revision4 leaves their Bluetooth template addresses unset. The
old ALSA aliases `CARD_0_DEV_30`/`31` were invalid Bluetooth MACs during the
GSI's AIDL-to-legacy-to-AIDL conversion and caused it to discard the entire
policy. The templates remain unattached and the donor primary HAL does not
implement external-device connection; this adds no SCO transport. Reserved
PCM30/31 rejection remains defensive code, but those are no longer policy
addresses. Keeping the templates and routes preserves SCO type ownership for
the GSI fallback lookup.
Complete primary profiles and real MM1 routing provide the
built-in fallback. These changes target the reported recursive policy crash;
**host checks cannot prove the disconnect crash is fixed**.

Analog jack discovery/routing is now wired by the current integrated builder:
stock MBHC calibration, framework input RRO, external wired output ports and a
pinned donor-HAL correction. See [jack implementation](../jack/README.md).
Physical insertion/removal and automatic routing acceptance remain pending.
Existing `a9ml-audio.sh headphone-wcd --user-present` and ESS manual routes
remain available for attended direct PCM testing. Earpiece is a framework
communication endpoint; ordinary media still selects speaker. This is not a
cellular-call bridge. The stock speaker tuning and DT remain unchanged. R6 replaces only the A9
machine-driver module; the other kernel modules and build-m boot stay intact.

## R6: completed speaker transport, drain tail and earpiece routing

The coordinator heard a clean speaker beep on r5 at macro gain78. All48,000
frames were written, with final hw_ptr47999/DRAINING. The Q6ASM pointer callback
returns completed_periods × period_size − 1 for nonzero pointers. R6 accepts
only the exact final one-frame tail (appl_ptr48000, hw_ptr47999, delay1), stable
for20ms in DRAINING. The waveform's final sample is zero. Cleanup still DROP/
closes the PCM. A remaining period, no progress, wrong state or ioctl error
still fails; drain has its own3second timeout and error snapshot. This addresses
the false drain error without changing the waveform or PCM start parameters.

Earpiece was only pops at gains54/66/78. The route already matches stock handset
RX0/AIF1→INT0→HPHL_OUT→WCD RX1→RDAC3→EAR; EAR PGA enables the PA through DAPM.
HPHL Switch selects its SoundWire input, not the headphone endpoint. No guessed
EAR PA switch or different DAI is added. CARD_0_DEV_24 remains a HAL alias to
physical MM1/PCM0, not a separate kernel DAI.

Two source mismatches are fixed as an earpiece candidate:

* Stock handset requests one RX0 channel; the old machine driver forced two.
  The machine now reads the selected RX macro lane count/mask, fixes RX0/RX1
  backend channels accordingly, and passes that mask to Q6AFE. Handset is mono
  mask0x1, WCD headphones stereo mask0x3, AUX speaker remains mono mask0x1.
  Missing/invalid lanes fail rather than opening a mismatched backend.
* Manual `off` left LO/AUX and macro AIF assignments from a preceding speaker
  test. It now clears WCD ports, RDAC switches, EAR mode and all six macro
  input muxes before selecting a new route. HAL `off` also clears those muxes.
  The builder explicitly stages the new manual script instead of inheriting
  the base vendor's older copy.

These are host-validated corrections, **not confirmed earpiece audibility**.
Gain54 (-30dB) and EAR_PA16 (-18dB analog), codec mode, physical EAR route and
quiet samples are unchanged. Even at macro78, this tone reaches EAR roughly
84dB below full-scale before transducer sensitivity; five-centimetre listening
can miss it. Do not infer a dead data path from pops alone. Capture backend
rate/channels/mask and the read-only EAR/DAPM state if the new route stays quiet.
The repeated open/close amp pops are still a separate hardware observation;
this revision does not claim to eliminate them.

## Build and host verification

Obtain the schema from
[the official AOSP 7.0 policy schema](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/main/audio/7.0/config/audio_policy_configuration.xsd).
The tested copy (SHA256 `499f50123e84f0b64b248af7a2827fa39a3c2fda2e9925d509316c191960cf5a`)
and exact expanded policy are in the task output. The builder requires this schema digest.

```sh
python3 scripts/mainline/audio/output/build.py \
  --schema ~/A9-build/mainline/audio-output-20261020/references/audio_policy_configuration.xsd \
  --xmllint /nix/store/kdfkjjlgys96rdbl4rqmrvygs9xfmnl5-libxml2-2.14.5-bin/bin/xmllint
python3 scripts/mainline/audio/output/test-host.py
python3 scripts/mainline/audio/output/test-card.py
```

The build runs `xmllint --xinclude --noout --schema`, checks device ownership
and route endpoints, rejects nonempty Bluetooth addresses that are not MACs,
verifies every changed image file and its metadata, and
runs `e2fsck -fn`. It then runs `check-vintf.py` against the rebuilt image,
reading all vendor/etc/vintf XML and every packaged vendor APEX's etc/vintf XML.
The duplicate-FqInstance check normalizes AIDL, HIDL and native declarations,
reports exact source owners, rejects conflicts, and requires one default
Bluetooth provider. It is not a full framework compatibility-matrix check.
The rejected revision1 image reproduces the exact duplicate in this check. QEMU uses real Android linker/libc with fake PCM/control I/O:
five focused groups cover pre-open routing/gain, earpiece/headphone mapping,
concurrent mic, failure cleanup and conflicting opens/passthrough. It also
checks the unmodified core executable's dependency closure with Android linker
`--list`; no HAL main function or binder service is started. Host namespace
resolution does not prove live SELinux/linkerconfig access on the phone.
The additional memfd check uses real shared mappings, descriptor duplication,
size seals and future-write protection under Android libc/QEMU. A separate
task-output replay of the exact vendor core and GSI AIDL client opens a primary
48 kHz PCM16 stereo stream after this fix; before it FMQ allocation fails.
That replay mocks service publication and thread priority, uses local interface
calls and opens no PCM device. Revision4 extends it to round-trip all26actual
HAL ports through the shipped GSI conversion library. Old SCO addresses
reproduce the exact malformed-MAC/BAD_VALUE chain; all26fixed ports pass.
The generic fallback's44.1kHz/empty-address output fails, while the configured
48kHz/ALSA-address speaker stream opens. It does not run the complete
AudioPolicyManager or prove phone routing or sound.

## Coordinator: read-only diagnostics before flashing

Run these **yourself** from an existing root Android shell. Do not pipe private
Bluetooth dumps or logs into public reports. The following reads can diagnose
the currently booted vendor before replacing it:

```sh
getprop init.svc.vendor.audio-hal-aidl
getprop init.svc.system.bt-audio-hal
getprop vendor.a9.audio.state
getprop vendor.a9.sensors.ready
getprop persist.bluetooth.system_audio_hal.enabled
getprop persist.bluetooth.bluetooth_audio_hal.disabled
getprop ro.bluetooth.a2dp_offload.supported
getprop persist.bluetooth.a2dp_offload.disabled
getprop ro.bluetooth.leaudio_offload.supported
getprop persist.bluetooth.leaudio_offload.disabled
service list | grep -E 'audio.core|bluetooth.audio|audio_flinger|audio_policy'
lshal | grep -E 'audio|bluetooth'
ps -A | grep -E 'audioserver|audio.service|bluetooth.audio'
dumpsys media.audio_policy
dumpsys media.audio_flinger
dumpsys bluetooth_manager
cat /proc/asound/cards /proc/asound/pcm
/vendor/bin/a9ml-audio.sh non-default
logcat -b crash -d -t 100
```

After booting the candidate, expect `android.hardware.audio.core.IModule/default`,
`IModule/bluetooth`, `IModule/r_submix`, `IConfig/default`, and
`android.hardware.bluetooth.audio.IBluetoothAudioProviderFactory/default` in
`service list`. AIDL services do not appear in `lshal`. The system `sysbta`
service may still exist, but the stack should select default with the property
false. `dumpsys media.audio_policy` must show a primary attached Speaker and
Earpiece, a primary output and Built-In Mic; Bluetooth A2DP devices belong only
to bluetooth. Inspect those before playback. `vendor.a9.audio.ready` must be1
without running the manual `load` command. If not, inspect its state, the sensor
ready property and kernel module/probe errors.

## Coordinator: capture AudioPolicyManager startup once

Revision3 boots and registers its HALs on the phone, but the coordinator reports
`AudioPolicyManager: 0x0`. Registration alone does not prove routing initialized.
Its startup capture identifies the invalid Bluetooth SCO addresses described
above. The GSI rejected the policy and used a generic default configuration,
whose speaker profile does not match the A9 HAL. Revision4 fixes that conversion
failure and retains the real speaker profile. Confirm the phone result and
retain the first initialization error if it still fails.

From the existing root shell on the new vendor, run once (no audible test):

```sh
/vendor/bin/a9-audio-startlog.sh
```

This enables verbose audio tags, clears main/system log buffers, starts a
private rotating capture **before** restarting audioserver, then saves policy
and AudioFlinger dumps plus ashmem-node/property checks after 15 seconds.
It prints the private output directory;
files use umask077 and the directory mode0700. Capture is limited to three
roughly2MiB files. It stops only its own logcat reader. It does not restart
SurfaceFlinger, change codec controls or play a tone. Verbose tag properties
last until reboot. Preserve raw logs locally; return the first sanitized
`initialize`/engine/default-output/FMQ error to the worker.

On an older vendor without this helper,
the equivalent one-shot command is:

```sh
umask 077; logcat -b main -b system -c; logcat -b main -b system -v threadtime -f /data/local/tmp/a9-audio-start.log -r 2048 -n 2 '*:V' & cap=$!; sleep 1; stop audioserver; sleep 1; start audioserver; sleep 15; kill "$cap"; wait "$cap" 2>/dev/null || :; dumpsys media.audio_policy > /data/local/tmp/a9-audio-policy.txt
```

Logcat tag filters do not expand `AHAL_*`, so the capture keeps every tag and
can be filtered afterward. A non-null policy dump must contain HW modules,
attached devices and an opened primary output before any playback acceptance.

## Coordinator: attended quiet tones, speaker then earpiece

**Only the coordinator runs playback, with the user present.**
R5 speaker sound is confirmed at gain78; r6 targets the false drain error and
corrects earpiece backend/route state. Start each route at gain54 again. Expected
completion is rc0, including an explicit Q6ASM tail notice when applicable.
The one-second waveform and peak32 are unchanged. See
[live diagnostics without rebuilding](LIVE-DIAGNOSTICS.md) for MM1 mixer,
in-flight PCM status, EAR/DAPM and Bluetooth-provider commands.

User present for the entire sequence. Remove headphones, keep the phone away
from the ear, pause all media, and stop if there is unexpected loudness or heat.
These are root-shell commands for the coordinator, not commands the worker ran.
Stop the framework audio clients before direct PCM to release MM1; leave ADSP,
the sensor owner and vendor.audio-hal-aidl running. The latter hosts the default
Bluetooth provider; stopping it causes expected provider lookup retries:

```sh
stop audioserver
sleep 1
/vendor/bin/a9ml-audio.sh off
/vendor/bin/a9ml-audio.sh speaker --user-present
/vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio get 'RX_RX2 Digital Volume'
/vendor/bin/a9-audio-pcm tone 0 Hisense-A9-Audio --user-present
/vendor/bin/a9ml-audio.sh off
```

The route sets macro gain54 (-30 dB) and music stream index1; the PCM helper
plays one second of a ramped 1 kHz triangle at peak32/32768 (about -60 dBFS).
The combined initial level is extremely quiet and may be inaudible. Successful
PCM completion records frontend consumption, not physical audibility. If PCM completes but is inaudible, repeat a single
tone at gain66 (-18 dB), then only if still needed at78 (-6 dB), using the same
quiet PCM samples. Re-select the route each time; do not change the amp tuning:

```sh
/vendor/bin/a9ml-audio.sh speaker --user-present
/vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio set 'RX_RX2 Digital Volume' 66
/vendor/bin/a9-audio-pcm tone 0 Hisense-A9-Audio --user-present
/vendor/bin/a9ml-audio.sh off
```

Earpiece, still away from the ear, starting again at the low default gain:

```sh
/vendor/bin/a9ml-audio.sh earpiece --user-present
/vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio get 'RX_RX0 Digital Volume'
/vendor/bin/a9-audio-pcm tone 0 Hisense-A9-Audio --user-present
/vendor/bin/a9ml-audio.sh off
start vendor.audio-hal-aidl
start audioserver
cmd media_session volume --stream 3 --set 1
```

If necessary, the same one-step66 then78 escalation can be applied to
`RX_RX0 Digital Volume`, with a new route selection and `off` after each tone.
Use physical PCM0 for both direct tests. EAR_PA Volume16 remains -18dB analog; keep it unchanged for the first retest.
A failed command should be followed by
`/vendor/bin/a9ml-audio.sh off` before investigating it; restore framework audio
with the two `start` commands above.

For framework output, keep volume at index1, play a short known quiet clip,
then inspect AudioFlinger and PCM status. `logcat -d -s A9AudioRoute` should show
MM1 route0 -> PCM0 ready1, then a close when the output enters standby. If
Bluetooth is connected there should be a bluetooth output/session instead,
without powering the speaker. Turn headset volume down before connecting,
select SBC/software A2DP if the codec selector is available, and repeat the
quiet clip. With playback paused first, turn the headphones off and verify
that audioserver's PID stays stable, primary speaker remains available and
resumed media goes to the speaker. Repeat once with active quiet playback to
exercise the reported fallback crash. Capture dumps before/after; Bluetooth
readiness and no crash are still device acceptance criteria.

Rollback: use the original camera-preview vendor with build-m boot. If restoring
the old GSI `sysbta` setup is intended, explicitly set
`persist.bluetooth.system_audio_hal.enabled=true` afterward; persistent property
changes otherwise survive replacing the vendor.

For a returned ALSA write failure, the adapter emits one `A9AudioRoute`
`MM1 write failed` line per open, preserving tinyalsa's errno/error text and
one native PCM status snapshot. It forwards the original call and return
unchanged. An internal tinyalsa recovery loop that does not return still
requires the existing FE trace. See `runs/audio-silent-fix-20261022.md`.
