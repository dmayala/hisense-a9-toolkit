# Integrated host build

From the desired merged source commit, run one command:

```sh
scripts/mainline/build-integrated.sh --out ~/A9-build/mainline/integration-build-20261020
```

Use a fresh output directory. Outputs are `boot.img`, `vendor.img`, their
`.sha256` sidecars, `SHA256SUMS`, `snapshot/`, `integrated-build.json`, and
per-feature logs. Images contain existing private firmware/modem seeds; retain
locally. No phone access or private `images-mac` input is used.

For a vendor-only replacement of explicit files, retain an existing integrated
vendor and pass `--overlay-only` to the same refresh path:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated \
  --base-vendor ~/A9-build/mainline/integrated-20261020k/vendor.img \
  --overlay /path/to/vendor-relative-overlay --overlay-only \
  --out ~/A9-build/mainline/integrated-20261020k5
```

This mode applies only that overlay, preserving unrelated subsystem files and
manifests. Existing file ownership, mode, timestamps and SELinux labels are
retained and verified by readback, followed by filesystem checking. It produces
only vendor artifacts; pair with the coordinator's existing boot. The image
copy uses COW when available and otherwise preserves zero blocks as sparse
holes to reduce host disk use without changing image bytes.

`--video` adds the experimental B10 Venus kernel probe from the staged modules
and pinned firmware in `--video-build` (default `~/A9-build/mainline/video-20261020`).
It is opt-in and has no Codec2 HAL yet. See [video/README.md](video/README.md) for
the host build and native decode phone steps.

The R4 composition supplies GPU/Mesa, Bluetooth, ADSP, thermal/health and Wi-Fi.
The current worktree refresh adds merged Wi-Fi/BT/session/logd/E Ink/key/AOD
scripts, keeping the original integrated session settings. Feature steps then
build and combine:

- B3 kernel watchdog/s2idle patch, its boot flag and `a9ml-suspend` helper;
- B6 sampled battery module, DT-compatible selection and init script;
- B2 sensors HAL from current motion/proximity sources;
- vibration module and HAL, enabled DT node, and TLMM67 released for reset;
- B9 modular media configuration, rear CAMSS/S5K3L6 DT and modules, and manual
  loader/capture tools, with only reset18 and rail93 released for camera GPIOs.

GNSS is included by default with the merged unsupported-extension crash fix
(`2ff5c31`). `--without gnss` omits it; `--gnss` remains accepted explicitly. `--without sensors`,
`--without vibration`, `--without suspend`, `--without battery`, `--without cameras`, and
`--without gnss` omit those feature steps; repeat the flag as needed. Omitting
sensors retains R4's older light-only HAL. R4 baseline Wi-Fi/BT/GPU are retained.

Each step reuses its feature's existing builder. Suspend makes one kernel with
R4-compatible config/release/symbol CRCs; external modules use that ABI. To
reduce disk use, the suspend builder can reuse the existing B3 kernel source
read-only after matching the current patch, while its objects are private.
If that cache is absent it uses the original full source-copy recipe and its
larger disk budget. Exact make argv: `suspend/kernel-command.json` and
`battery/module-command.json`; vibration command is in its existing builder.
The final snapshot contains the cumulative DT and all added external modules;
module/thermal kernel hashes match the rebuilt kernel. Camera modules use a
separate modular configuration at `cameras/obj/.config`, as in the B9 recipe;
`snapshot/config` continues to describe the actual built-in kernel. Future features add a
build/stage step in `build-integrated.py` before vendor refresh or boot assembly.

Suspend observability is finalized after feature packaging by default
(`--suspend-observable`, disabled by `--no-suspend-observable` or `--without suspend`).
This also repairs cached `--kernel-build` boots whose suspend flags were only
in `extra_cmdline`. The five suspend flags and Android storage/slot arguments
fit in the primary512-byte field; the snapshot DT comes from that final boot.
Skipping this step retains any settings already in the input boot. See
[suspend/README.md](suspend/README.md#round-7-primary-cmdline-and-a6l-leads) for
the A6L comparison and coordinator RTC/pstore procedure.

To finish packaging after a successful kernel build, pass
`--kernel-build DIR` pointing to its already packaged `suspend/` directory.
This reuses its boot, helper and snapshot; other feature steps still build.

The coordinator substitutes this boot/vendor pair and snapshot into the existing
owned slot-B POC path, retaining its existing private radio identity handling.
Check boot completion, normal E Ink open/redraw and fault latches, connected
Wi-Fi/HTTP, BT, ADSP and sensors before new feature tests. Then follow
`vibration/README.md`, `pmic/battery/README.md`, `suspend/README.md` and
`../../docs/mainline-cameras.md` for the
respective phone tests. Suspend recovery, physical vibration, B2 motion and B6
charging remain hardware-unqualified until the coordinator supplies results.

Venus B10 is now included by default (`--without video` omits it). When cameras
are included, one media-module build uses the union of camera and Venus config;
common V4L2 modules are never overwritten with a differently configured set.
Pinned public firmware comes from `--video-build DIR` (default local
`video-20261020`). Camera and video remain native probes, with no Android
camera provider or Codec2 service yet.

`--audio` adds A5's experimental WCD/ESS card, speaker amp, MM1/MM2 primary HAL
policy and manual route tools. It rebuilds the current external audio modules
and patched Q6AFE from source against private prepared headers (the legacy
`--audio-build` cache argument no longer supplies generated modules), applies its DT cumulatively,
and keeps every output off until explicitly selected with the user present.
See [audio/README-poc.md](audio/README-poc.md) for silent mic checks and attended
playback. Audio is opt-in; automatic jack policy remains unfinished.
The final vendor's card/Q6AFE module SHA256 and vermagic must match this run's
fresh modules, and its helper must match current source; a mismatch fails the
build. Results are recorded in `audio/vendor-check.json`.
For an explicitly authorized smaller disk reserve, the late vibration packager
accepts `A9_VIBRATION_MIN_FREE_GIB` (default15), in addition to its 1GiB packaging
budget. Other builders retain their existing disk admissions.

`--cellular` adds B8's AIDL V4 radio HAL, IPA4.2/rmnet modules, stock `ipa_fws`
and cumulative IPA DT using `cellular/build-images.py --ipa --stage-only`.
It stages into the shared overlay before the single vendor refresh; later
vibration DT changes and all module hashes remain in the final snapshot.
The HAL builder reuses cached AOSP headers and its incremental objects. IPA
builds only its small external source tree against existing R4 objects, with
no full kernel/object copy or additional vendor image. The refreshed vendor
retains both GNSS and radio executable context mappings. Use, for example:

```sh
scripts/mainline/build-integrated.sh --cellular --audio \
  --kernel-build /path/to/already-packaged/suspend --out /path/to/fresh/output
```

Cellular is opt-in (`--without cellular` overrides `--cellular`). Its runtime
startup still requires `setprop vendor.a9ml.cellular 1` inside the coordinator's
existing MPSS/RAM-copy session. It does not start MPSS or set up modem stores.
SIM, registration, framework data and SMS remain phone-untested; follow
[cellular/README.md](cellular/README.md) for the first probe. Calls/IMS deferred.

For a low-disk suspend diagnostic update of a delivered pair, run:

```sh
scripts/mainline/build-integrated.sh \
  --update-suspend ~/A9-build/mainline/integration-aw8695-20261020 \
  --out ~/A9-build/mainline/integration-suspend-observable-20261020
```

This retains every existing feature, including AW8695. It reuses the built-in
pstore/ramoops support and stock reserved memory, enables console logging during
suspend, adds kmsg progress, and hands watchdogd-gsi ownership to/from the helper.
Only the static helper is compiled; no kernel object tree is copied. The normal
B3 package and `--kernel-build` reuse path receive the same diagnostic settings.
See [suspend/README.md](suspend/README.md#observable-integrated-retry) for previous-boot
log retrieval and the limits of what the markers prove. Use the new matching
snapshot/null DTBO; preserve the already delivered images.

`--enforcing` opts into the B4 candidate policy, dedicated helper domains,
actual vendor inode relabelling and enforcing boot arguments. Functional
services enforce; the coordinator's development root-shell domain alone is
permissive for diagnostics. The default
remains permissive. `--enforcing-platform-cache DIR` must describe the actual
GSI system policy; the default is the cached LOS v8 policy. See
[sepolicy/README.md](android/sepolicy/README.md) for the incremental command,
compiled-policy selection and coordinator qualification steps. This flag is a
host-built test candidate, not a phone enforcing-parity pass.
