# FIX4: WCD right channel and endpoint stop

The attended merged-vendor result confirmed speaker playback without an end
pop. Earpiece was heard at macro114 and WCD headphones at macro104, left only;
both direct-tone endpoints popped at stop. FIX4 was subsequently confirmed:
EAR twice and headphone left/right/stereo all heard with no stop pops, using
manual commands because the launcher did not execute. [FIX5](FIX5.md) fixes
that launcher and jack detection. FIX4 retains the proven speaker
sequence, FIX3 donor/Q6 corrections and first-write-error logging.

## Right channel

The old direct tool already wrote two identical interleaved samples per frame.
HPHR Switch, HPHR_RDAC Switch, INT1 input RX1 and AIF1 RX0/RX1 selection were
also present. The missing selectors were RX INT0 DEM MUX and RX INT1 DEM MUX.
Stock mixer_paths_qrd.xml headphones explicitly selects CLSH_DSM_OUT on both.
The mainline lpass-rx-macro driver defaults CFG1 to0x64 (NORMAL_DSM_OUT), but
only CLSH_DSM_OUT has a DAPM route from INT0/1 MIX2 to HPHL/R_OUT. Earpiece
selected INT0 and left it selected for the subsequent headphone test. INT1
remained disconnected, explaining left-only playback. Both headphone paths
(HAL XML and manual helper) now explicitly select both DEM muxes. No kernel
change is needed for this omission; attended stereo confirmation passed.

## Shutdown

a9-audio-quiet.h shares the verified mixer implementation between the HAL
adapter and direct tool. It ramps the selected RX source(s) to index0 (-84dB)
in at most6dB steps at400us, settles2ms, disables the endpoint and its RDAC
switch(es), then settles2ms before PCM teardown. Both headphone gains reach
zero before either PA is disabled. Endpoint/RDAC removal invokes synchronous
WCD DAPM PA-off events while the PCM remains open. BE selection and SoundWire
port controls remain intact until normal close. Speaker uses its existing
RX2 ramp and AW Off sequence unchanged.

Stock WCD EAR PA PRE_PMD notifies RX0 mute, POST_PMD waits7ms; HPHL/HPHR
PRE_PMD notify RX0/RX1 mute, POST_PMD wait20ms without companders (7ms with
companders), then finish PA/class-H cleanup. The mainline codec already
provides these discharge waits. Our explicit source attenuation supplies the
mute before those events; this is not a claim that stock uses our gain ramp.
The attended result confirmed pop suppression on both endpoints.

Direct PCM must quiesce before DRAIN as well as DROP/close: DRAIN itself can
stop the backend. The last at most40ms of queued tone is attenuated during this
step. Returned transfer errors also quiesce before DROP/close. Direct playback
leaves PA/RDAC quiet; reselect the manual route before another tone. Probe and
capture never select or quiesce a playback endpoint.

## Attended check

```sh
bash /home/user/A9-build/mainline/plain-integ-audiofix4-20261022/audio-jack-earpiece.sh --user-present
```

Use the new vendor with the same plain-deadline-logfix1-20261022 boot. The
script prompts for earpiece (twice), headset (left, right, both), and unplug.
It reads the actual Headphone Jack control. Waveform peak is3276/32768
(-20dBFS), with5ms fades. Macro74(-10dB) for EAR plus its existing -18dB
analog attenuation reproduces the previously heard -48dB test level. Macro64
(-20dB) for HPH reproduces the previously heard -40dB level. The script sets
these values explicitly and restores the original gains after route shutdown.
It captures selected mixer values, live PCM status/hw_params and relevant
DAPM widgets in one private host file; audioserver is restored per phase.

The direct CLI also accepts tone-left and tone-right; each remains stereo
PCM16/48kHz with the unused slot zero. No worker phone operation or playback
was performed. Existing Android QEMU route/PCM fixtures, shell syntax,
metadata/readback and ext4 checks passed. Build with output/build-fix4.py;
stage-current.py now also rebuilds the direct PCM tool for future integration.

Local detailed evidence and checks:
/home/user/A9-build/mainline/plain-integ-audiofix4-20261022/report.md.
