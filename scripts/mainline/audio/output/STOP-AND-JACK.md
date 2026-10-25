# Speaker standby and the next attended endpoint check

FIX3 speaker playback and this standby correction are confirmed by the user:
startup is clean and shutdown no longer pops. This keeps Music-before-open,
gain75, tinyalsa negotiated-config correction, Q6 cursors and first-error log.

Stock REA finds pcm_close before route/device reset in out_standby_l0x148338
and stop_output_stream0x148638. Stock WCD AUX PRE_PMD0x102718 sends parent
event1/arg0x20001; RX event0x1014b0 sets RX2 main/mix bit4 mute. AUX POST_PMD
waits2ms. Stock AW87519 hardware-off resets low and waits approximately2ms;
scene-off can optionally replay an available Off configuration. This evidence
does not establish a normal stock software gain ramp or the exact electrical
cause of the observed pop.

Our external AW DAPM widget can reset before WCD AUX's own teardown. To make
input attenuation explicit before reset, the adapter now ramps the current RX2
control down to index0 (-84dB) in steps of at most6dB,400us apart, settles2ms,
selects amp Off, settles2ms, then calls the real pcm_close. Route/pin cleanup
still follows close. The next speaker selection sets gain75/Music as before.
Only a real active speaker close performs this sequence; parameter queries
and capture do not. Failures log and still attempt amp reset
and normal close. The existing fixture verifies RX2zero/ampOff before the real
close. The added ramp is not a measured stock ramp. [FIX4](FIX4.md) shares it
with earpiece/headphone preclose attenuation and synchronous PA shutdown.

The plain integration recipe now stages the current audio closure from source,
including the corrected donor library and both Q6ASM modules, instead of
inheriting the old silent audio files. To package a new vendor while retaining
the proven boot:

```sh
python3 -I scripts/mainline/plain-integ-20261022.py package \
  --out /home/user/A9-build/mainline/plain-integ-audio-merged-20261022 \
  --reuse-boot /home/user/A9-build/mainline/plain-integ-20261022
```

This delivered directory already exists; choose a new --out for a later build.
The recipe refuses to replace a delivered vendor. The generic audio builder
also carries the donor/Q6 corrections; the A5 module builder includes the Q6
patch when rebuilding its closure.

For the next attended session, after the coordinator boots the FIX4 vendor
and the same deadline-logfix1 boot on B, one host command runs both checks:

```sh
bash /home/user/A9-build/mainline/plain-integ-audiofix4-20261022/audio-jack-earpiece.sh --user-present
```

It prompts unplug/earpiece, insert/headset, then unplug. It reads the real ALSA
Headphone Jack indication, selects the earpiece or WCD headphone route, and
plays the one-second -20dBFS peak tone on physical PCM0. Earpiece plays twice;
headphones play isolated left, isolated right, then both. Macro gain74 for
earpiece and64 for headphones match the previously heard attended levels;
both original macro gains are restored afterward. Each phase stops/restores
audioserver while retaining the HAL/Bluetooth provider and clears its manual
route. Jack-in is required before headphone playback; jack-out is checked.
Results go to one private host file. It does not inject a jack event, flash,
qualify automatic framework analog routing, or implement a cellular call.
Report which tones were heard and any transition pops. Worker verification
uses host/Android syntax and the production route/PCM fixtures; the FIX4
script has not run on the phone.
