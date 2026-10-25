# FIX5: attended launcher and WCD jack startup

The user heard both earpiece tones, isolated headphone left and right, and
stereo on FIX4, with no stop pops. This confirms the DEM route correction
and shared quiesce sequence. The tones were run manually: the launcher log
contained only its header. Headphone Jack remained zero while inserted.

## Launcher

The host now uploads its embedded phase script to a private temporary device
path, then submits a short command through the existing loopback root shell.
The command is produced on the adb-shell side; adb stdin is /dev/null.
This removes the host script-pipe dependency and prevents adb from consuming
prompt input. Capture includes HOST_BEGIN, upload status, HOST_DISPATCH,
device PHASE/JACK_RESULT/TONE records, transport status and PHASE_RESULT.
Missing terminal input or missing remote results now produce explicit errors.
The old header-only file cannot identify whether prompt EOF or transport loss
prevented execution; both are addressed without claiming a measured adb bug.

Interactive use:

```sh
bash /home/user/A9-build/mainline/plain-integ-audiofix5-20261022/audio-jack-earpiece.sh --user-present
```

agent can run individual attended steps without a terminal prompt, after
confirming the physical step (unplug and hold earpiece; insert headphones;
unplug respectively):

```sh
bash /home/user/A9-build/mainline/plain-integ-audiofix5-20261022/audio-jack-earpiece.sh --user-present --phase earpiece
bash /home/user/A9-build/mainline/plain-integ-audiofix5-20261022/audio-jack-earpiece.sh --user-present --phase headset
bash /home/user/A9-build/mainline/plain-integ-audiofix5-20261022/audio-jack-earpiece.sh --user-present --phase unplug
```

Every tone selects the route again, then applies EAR74 or HPH64 macro gain.
The prior loop already contained reselect, but did not execute in the failed
launcher. The direct tool continues to leave PA/RDAC quiet after a tone.
Jack readback is independent of the operator-confirmed listening step: a
mismatch is logged FAIL and returns nonzero, while attended tones still run.
No artificial jack event or false detection success is introduced.

## Jack detection

Our card called qcom_snd_wcd_jack_setup only on RX_CODEC_DMA_RX_0. The shared
helper creates the jack and returns success there, but its switch calls codec
set_jack only on TX_CODEC_DMA_TX_0..3. WCD set_jack is what calls
wcd_mbhc_start, which initializes plug polarity/debounce, bias and the detector
clock. Thus the old ready=1 log proved control creation, not a started MBHC.
The card now calls the helper on the existing WCD TX3 link. Codec startup
errors still propagate. This is a machine-module change; no kernel Image,
DT, polarity, threshold, IRQ or audio sample/PA sequence changes.

Stock WCD937x separates MBHC init from detection start too: init0x1047e0
calls wcd_mbhc_init; hs_detect0x1045e0 explicitly calls wcd_mbhc_start. Merely
initializing the codec is insufficient. The saved stock evidence and exact
mainline source excerpts are in the FIX5 output directory. Generic helper
pin names differ from our manually controlled endpoints, producing the
previous unknown-pin warnings; this is separate from starting detection and
does not prevent ALSA jack reporting. Automatic framework analog routing
is not part of this attended manual check.

## Artifact and checks

build-fix5.py overlays only snd-soc-a9-audio.ko on the confirmed FIX4 vendor,
retaining the existing plain-deadline-logfix1-wdt boot. The fixed host script
works with FIX4 for listening checks; the new vendor is needed for jack start.

The existing card fixture compiles the production init and actual Qualcomm
helper: RX0 reproduces controls-without-start, TX3 calls set_jack, and failures
propagate. A bounded host replay of the real launcher/body with fake adb/PCM
checks EAR twice, HP left/right/both with reselect each time, jack failure kept
separate, empty transport and absent prompt input. No phone or playback was
accessed. Module ABI, Android shell syntax, metadata/readback and fsck pass;
physical jack in/out and the corrected launcher remain pending attended use.

FIX4 is merged into main as619b4c2. Current source/evidence hashes and handoff:
/home/user/A9-build/mainline/plain-integ-audiofix5-20261022/report.md.
