# A9 volume configuration

The AIDL core used by r6 has no engine XML. It converts the legacy volume
files into 14 groups, marking the 12 public groups `-1/-1` so AudioService can
initialize them. Those values are deliberate deferred indexes, not malformed
curve points. APM's `checkAndSetVolume` logs the supplied invalid-range message
and rejects native gain application while a range remains negative.

`volume-policy.py` supplies `audio_policy_engine_configuration.xml` with explicit
SDK ranges: music 0..15, ring/notification 0..7, and the standard ranges for the
other streams. Names match the default phone strategies. No custom strategies
or CAP configuration are added. The HAL loads this file before falling back
to legacy volumes; a fresh boot loads it into the HAL's cached config reply.

The speaker music curve is also copied into legacy `audio_policy_volumes.xml`,
so both configuration paths agree. It uses normalized points
`0,-9600; 1,-3600; 33,-2400; 66,-1200; 100,0` (millibels).
Other stream/device curves and the shared reference tables are preserved.
The builder automatically stages these files after reading the base volumes.

The original volume candidate used RX gain54 (-30 dB). FIX2 changes only the
speaker RX2 default to stock QRD index75 (-9 dB), 21 dB above that baseline;
the software volume curves below remain unchanged. The table describes the
original gain54 baseline. Direct quiet-tone samples stay
unchanged. For a full-scale music signal, the resulting digital peak ceiling is:

| Music step | Software gain | Including RX gain54 |
| --- | ---: | ---: |
| 0 | mute / -96 dB fallback | mute |
| 1 | -34.125 dB | -64.125 dB |
| 2 | -31.5 dB | -61.5 dB |
| 5 | -24 dB | -54 dB |
| 10 | -11.647 dB | -41.647 dB |
| 15 | 0 dB | -30 dB |

This lifts music step1 by about19 dB and step5 by8.5 dB versus r6, while
preserving its maximum digital level. The attended r5 reference tone had a
roughly -66 dBFS peak after RX gain78; a full-scale framework signal at step1
is about2 dB above that reference. Real media peaks vary. The -60 dBFS direct
test waveform should not be treated as the normal level of Android content.
These are digital attenuation bounds, not calibrated acoustic SPL limits.

## Integration with integ/20261021n

Cherry-pick the volume commit onto agent's branch. It changes the audio
builder, adds this generator/test/documentation, and touches no SELinux or memfd
implementation. The staged configuration overlay is small; no standalone vendor
image rebuild is needed while the integrated enforcing vendor is being built.
From that worktree, using the retained r6 configuration as input:

```sh
python3 scripts/mainline/audio/output/volume-policy.py \
  --source-etc ~/A9-build/mainline/audio-output-20261020/vendor-overlay/etc \
  --dest-etc ~/A9-build/mainline/audio-output-20261020/volume/vendor-overlay/etc
python3 scripts/mainline/audio/output/test-volume-policy.py \
  --source-etc ~/A9-build/mainline/audio-output-20261020/vendor-overlay/etc \
  --result ~/A9-build/mainline/audio-output-20261020/volume/gain-steps.json
```

Merge that small overlay into the **final integrated vendor**, including the
new `/vendor/etc/audio_policy_engine_configuration.xml`. Label it
`u:object_r:vendor_configs_file:s0`, mode0644, root:root, as for the other audio
policy XML. The normal audio builder handles this metadata. Existing AIDL core
and policy modules remain installed. Do not replace agent's integrated image
or rebuild it from the old camera-preview vendor just to add these XML files.

## Coordinator verification

No worker phone command or playback is authorized. On a fresh integrated boot,
with media paused, the coordinator can check indexes without playing samples:

```sh
for stream in 2 3 5; do
  cmd media_session volume --stream "$stream" --set 1
  sleep 1
  cmd media_session volume --stream "$stream" --get
  cmd media_session volume --stream "$stream" --set 2
  sleep 1
  cmd media_session volume --stream "$stream" --get
  cmd media_session volume --stream "$stream" --set 1
done
dumpsys media.audio_policy
dumpsys audio
```

Expected: 1 then2, music range0..15 and ring/notification0..7, valid native volume
group ranges, and no negative-range gain rejection on the fresh boot. Test music
mute separately at step0; keep the user present for any subsequent playback and
begin at step1. Do not increase RX hardware gain for framework volume testing.
Ring and notification may share indexes through the ordinary phone aliases.

The startup `-1/-1` message alone does **not** establish why Java's
`getStreamVolume` stays5. AudioService maintains those indexes separately and
can reject a set request for fixed-volume, AppOps or DND reasons. Its native
range initialization should normally resolve the deferred values. The host
checks prove the corrected HAL reply and curves, not the phone's Java/Binder
set/get behavior. If indexes still stick after this and the enforcing memfd
fix, return only sanitized `dumpsys audio` fixed/full-volume device flags,
volume-event/reinit errors, and first `AudioService` AppOps/DND error. Preserve
raw dumps privately. Do not hide that remaining symptom by changing UI output.

Sources: AOSP [Config](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/android16-release/audio/aidl/default/Config.cpp),
[legacy conversion](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/android16-release/audio/aidl/default/AudioPolicyConfigXmlConverter.cpp),
[APM](https://android.googlesource.com/platform/frameworks/av/+/refs/heads/android16-release/services/audiopolicy/managerdefault/AudioPolicyManager.cpp),
[AudioService](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/core/java/com/android/server/audio/AudioService.java),
[engine schema](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/android16-release/audio/policy/1.0/xml/audio_policy_engine_configuration.xsd).
Local shipped-HAL replay and attenuation tables are under the task's `volume/`
output. The replay mocks registration/path lookup and exits after IConfig; it
starts no Binder service, opens no PCM and runs no complete AudioPolicyManager.
