# A9 analog jack detection

Current integration consumes stock MBHC calibration, an input-event framework
overlay, external wired output ports, and a pinned donor-HAL correction. See
[stock addresses and host results](../../../../runs/headphone-jack-20261025.md).
FIX5's existing TX3 detector startup is retained; no GPIO detector or extcon
poller is added. No output is enabled simply because the jack is inserted.

`build.py --out NEW_DIR` builds the small ARM64 correction and signed static
RRO. The donor SHA is fixed; only the original base port-population body and
one backend-store instruction change. C/assembly are retained alongside the
source-equivalent intent; a future complete HAL source build should implement
these overrides directly. The existing WCD PCM25 adapter route remains intact.
The local overlay signing key is reused between builds, outside git.

`test.py --stage DIR` replays the actual original/patched function bytes under
Android ARM64 QEMU with synthetic objects and status stubs. It never enters HAL
main or opens a mixer/PCM/device. It also checks a private copy of the real DTB,
policy port ownership, APK signature/resource data and Android idmap resolution
with its normal overlayable/policy checks. Physical plug/unplug, Android device
availability and ongoing-stream rerouting still need the coordinator's next
owned run. Headset microphone capture remains a separate acceptance gap.

`build14.py` builds this payload, patches the two DT properties before boot
repacking, and installs the HAL, primary policy and RRO with standard vendor
overlay labeling. Rebuild boot and vendor; replacing only a module is insufficient.
The bare historical audio-only recipes do not replace an already packaged boot.
