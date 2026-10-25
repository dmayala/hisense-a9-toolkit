# Coordinator: live audio diagnostics without rebuilding

Run these yourself from existing root Android shells. The worker runs no phone
commands. These reads do not select a route, change gain, stop a service or play
samples. Keep output private. Capture during a separately authorized, attended
write; this block never invokes playback.

```sh
umask 077
out=/data/local/tmp/a9-audio-live-$(date +%Y%m%dT%H%M%S)
mkdir -p "$out"
chmod 0700 "$out"
{
  for name in \
    'RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' \
    'RX_CODEC_DMA_RX_1 Audio Mixer MultiMedia1' \
    'SEC_MI2S_RX Audio Mixer MultiMedia1' \
    'RX_MACRO RX0 MUX' 'RX_MACRO RX1 MUX' 'RX_MACRO RX2 MUX' \
    'RX INT0_1 MIX1 INP0' 'RX INT0 DEM MUX' 'RX_EAR Mode Switch' \
    'RDAC3_MUX' 'EAR_RDAC Switch' 'EAR_PA Volume' 'RX HPH Mode' \
    'HPHL Switch' 'HPHR Switch' 'HPHL_RDAC Switch' 'HPHR_RDAC Switch' \
    'RX_RX0 Digital Volume' 'RX INT2_1 MIX1 INP0' \
    'RX_RX2 Digital Volume' 'AUX_RDAC Switch' 'LO Switch' \
    'CLSH PA Switch' 'Speaker Switch' 'Earpiece Switch'; do
    echo "CONTROL <$name>"
    /vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio get "$name"
  done
} > "$out/mm1-mixer.txt" 2>&1
{
  getprop init.svc.vendor.audio-hal-aidl
  getprop init.svc.audioserver
  getprop init.svc.system.bt-audio-hal
  getprop persist.bluetooth.system_audio_hal.enabled
  service list | grep -F android.hardware.bluetooth.audio
  service check android.hardware.bluetooth.audio.IBluetoothAudioProviderFactory/default
  service check android.hardware.bluetooth.audio.IBluetoothAudioProviderFactory/sysbta
} > "$out/bluetooth-provider.txt" 2>&1
{
  i=0
  while [ "$i" -lt 30 ]; do
    echo "SAMPLE $i"
    cat /proc/asound/card0/pcm0p/sub0/status
    i=$((i + 1))
    sleep 0.1
  done
  cat /proc/asound/card0/pcm0p/sub0/hw_params
  cat /proc/asound/card0/pcm0p/sub0/sw_params
} > "$out/mm1-status.txt" 2>&1
echo "Private diagnostics: $out"
```

Run sampling in a second shell while the writer is active. A closed status
cannot explain a running failure. For a single immediate snapshot:

```sh
cat /proc/asound/card0/pcm0p/sub0/status \
    /proc/asound/card0/pcm0p/sub0/hw_params \
    /proc/asound/card0/pcm0p/sub0/sw_params
```

Expected speaker routing: RX codec DMA1/MM1=1, RX codec DMA0/MM1=0,
secondary MI2S/MM1=0, RX2=AIF2_PB, INT2=RX2, speaker pin=1. Earlier images use
gain54; FIX2 uses stock QRD gain75 and Music mode before PCM open. Capture the
actual value during playback. AFE
port0xb032 is RX_CODEC_DMA_RX_1; RX0 is0xb030 and WSA is a separate port family.
The frontend should be48kHz stereo S16 and the speaker backend48kHz mono S16;
the machine driver deliberately fixes the AUX backend to mono.

PREPARED/state2 with queued appl_ptr indicates the stream has not entered
running state. RUNNING/state3 with static hw_ptr and a growing/full appl_ptr
indicates no hardware consumption. Advancing hw_ptr with no audible tone
directs investigation downstream. A timeout alone does not establish that an
ASM open, RUN command or period callback was absent. PREPARE normally maps ASM
buffers, opens the stream and sets up ADM routing; successful operations do
not all have log messages in this driver.

R5 logs PREPARED, PLAYBACK_AUTOSTART and WRITEI_ERROR snapshots. Autostart is
requested at960frames, independently of filling the1920frame ring. Check the
state and pointers; the AUTOSTART label alone is not proof of progress. If it
still stalls, return the first transfer error, queued frame count and status.

The default Bluetooth provider is hosted in vendor.audio-hal-aidl. Stopping
that service for direct PCM removes the provider even if sysbta remains running,
so the stack can retry default every second. R3 and r4 have identical Bluetooth
service/library/property/manifest bytes. Two bluetooth.audio names alone are
not a conflict: default and sysbta are different instances. Check their exact
names and whether default is available while the core HAL is running.

Pause media before a coordinator-owned direct test and stop audioserver only;
leave vendor.audio-hal-aidl running to retain its provider. If PCM0 is busy,
read status/owner_pid and diagnose its owner before stopping another service.
After an earlier test that stopped the HAL, restore it with these root-shell
commands (they do not invoke playback):

```sh
/vendor/bin/a9ml-audio.sh off
start vendor.audio-hal-aidl
start audioserver
service check android.hardware.bluetooth.audio.IBluetoothAudioProviderFactory/default
```

If default is still unavailable with a running HAL, or the stack still retries
while root finds default, return the exact service names and first sanitized
registration/servicemanager/SELinux denial. Keep raw Bluetooth logs private.

For ASM/ADM/callback timing, optional `a9-audio-fe-trace.sh` records8seconds of
module-qualified kprobe events in its own trace instance: FE prepare/trigger,
ASM open/run/write and return codes, ADM route/matrix and frontend callbacks. It does
not play samples or change audio services, routes or gain. Run it in a second
root shell before the coordinator's separately authorized attended write:

```sh
/vendor/bin/a9-audio-fe-trace.sh
```

This is in r5; on r4 the coordinator can transfer this same standalone shell
script using their existing method and run it through `/system/bin/sh`, without
flashing or rebuilding the kernel. It requires writable tracefs, KPROBE_EVENTS
and the named loaded module symbols. Unsupported probes fail and cleanup
removes only its own events/instance. It refuses an existing diagnostic owner;
it leaves other trace instances alone. Keep the trace private. No worker phone
execution or live-kernel qualification is claimed. Callback opcode0x1009 is the
client WRITE_DONE event in the matching host q6asm header; check against that
header rather than confusing it with a raw APR opcode.
The matching q6routing source does not propagate q6adm_matrix_map's return
value, so a successful route return alone does not prove a successful matrix
map; inspect matrix_ret as well.

## Earpiece follow-up on the current boot

During the separately attended earpiece write, repeat the mixer/status block
above. Expected Earpiece=1, Speaker/Headphones=0, RX0/MM1=1, RX1/MM1=0,
RX0=AIF1_PB, RX1/RX2=ZERO, INT0=RX0, DEM=CLSH_DSM_OUT, EAR mode1,
RDAC3=RX1, EAR_RDAC/HPHL/CLSH=1, LO/AUX/HPHR=0. On r5 manual speaker→ear
can leave LO/AUX/macroRX2 enabled; r6 resets them. Read the most recent backend
lines yourself (raw output private):

```sh
dmesg | grep -E 'A9-CARD (hw_params|CPU)|A9-Q6AFE DMA'
```

R6 should report WCD Headset Earpiece rate48000/channels1/width16 and DMA
port0xb030/mask0x1; speaker stays0xb032/mono/mask0x1. Physical direct playback
is PCM0 for both. CARD_0_DEV_24 is only the HAL's virtual route selector.

To capture power state and register snapshots without changing registers:

```sh
umask 077
out=/data/local/tmp/a9-ear-read-$(date +%Y%m%dT%H%M%S)
mkdir -p "$out"
chmod 0700 "$out"
for root in /sys/kernel/debug/asoc/*; do
  [ -d "$root" ] || continue
  find "$root" -type f -path '*/dapm/*' | while read -r file; do
    case "$file" in
      *EAR*|*RDAC3*|*RX1*|*HPHL_OUT*|*INT0*|*Earpiece*)
        echo "DAPM $file"; cat "$file" ;;
    esac
  done
done > "$out/dapm.txt" 2>&1
for root in /sys/kernel/debug/regmap/*; do
  case "$root" in
    *a600000*|*sdw*|*wcd*)
      [ -f "$root/registers" ] || continue
      echo "REGMAP $root"; cat "$root/registers" ;;
  esac
done > "$out/registers.txt" 2>&1
echo "Private EAR snapshots: $out"
```

Debugfs names/access vary; an empty capture is not proof the EAR PGA is off.
Run while writing, since DAPM powers down after close. Return only sanitized
power states for RX INT0/HPHL_OUT/RX1/RDAC3/EAR PGA/EAR/Earpiece and backend
parameters. Do not write raw codec registers. The optional trace helper remains
available if frontend pointers fail to advance.

## Attended plain-integ capture (2026-10-22)

`build-live-diag.py` copies the proven plain-integ vendor and replaces only
`lib64/a9-audio/liba9audioroute.so` with the first-error diagnostic adapter.
The plain-integ boot is reused without changes. Output:
`/home/user/A9-build/mainline/plain-integ-audiodiag-20261022/`.

When the user has returned and the coordinator has booted that pair on B:

```sh
bash /home/user/A9-build/mainline/plain-integ-audiodiag-20261022/audio-live.sh
```

This is one host launcher, using the existing root TCP channel. It copies
Andromeda.ogg to `/data/local/tmp/t.ogg`, arms the existing FE trace, starts
Twelve, samples PCM status/parameters and two sets of mixer **values**, DAPM
widgets, amp reset GPIO and read-only regmap registers, and collects kernel
and HAL logs into one private host file. It stops its test player afterward.
An optional first argument selects the output filename. Hold playback while
the user is AFK. Host syntax checks do not authorize worker phone access.

`build-fix2.py` builds the machine/amp pair and adapter, runs the existing
focused host fixtures, and replaces five files in the audiodiag vendor:
the two modules, adapter, mixer XML and manual route helper. It preserves
metadata and verifies readback and ext4 consistency. It retains the first
returned-write diagnostic and the existing FE trace helper. The selected
plain-integ 2 GiB free-space floor is preserved, with a 500 MiB build budget.

FIX2 uses the same `audio-live.sh`, defaulting to the directory containing
the launcher. To choose the image directory explicitly, after the user
returns and the coordinator has booted FIX2 on B:

```sh
bash /home/user/A9-build/mainline/plain-integ-audiofix2-20261022/audio-live.sh \
  --image-dir /home/user/A9-build/mainline/plain-integ-audiofix2-20261022
```

An optional filename may follow `--image-dir DIR`. This parameter selects
the host output directory only; it never flashes or boots an image. Music
reports the requested scene, while GPIO/register/DAPM snapshots show physical
power state. Five failed amp writes still return an error and reset; stock's
scene replay discarded that final error. Stock monitor/actflag handling and
platform calibration/bit-width differences remain outside this candidate.
