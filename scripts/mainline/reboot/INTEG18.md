# INTEG18 host delivery — 2026-10-24

Built from main `4e868cf0b5bc4b743eceb4832e0a7728dae46b65`.
Main was rechecked before the kernel build at `2026-10-24T14:33:46.177573+00:00` and
immediately before assembly at `2026-10-24T14:35:50.634591+00:00`; both selected
that commit. Source and runtime pins are in `integrated-build.json`.

Includes `1f11850` (stock mapped listener extent and SCM error preservation),
`7a71799` (SWEEP17 software camera recording and partial capability evidence),
`a8ef6d9` (early kernel/pstore and encoder progress diagnostics), and
`4e868cf` (prefer software AVC/HEVC encoders). Hardware encoder XML rank is
4096, behind Android software video rank 512; hardware decoders retain 256.
Android 16 Codec2InfoBuilder applies XML rank over component traits. The
row 18 native probe still uses exact hardware component names.

Uses the INTEG17 recipe: `build14-kernel.py`, fresh FBE trace payload, and
`build14.py` generation 18 with INTEG16 requirements plus subsequent fixes.
INTEG17 supplies the immutable binary baseline. The kernel and logger are
freshly built; runtime sources and acceptance APKs are refreshed from main.
The isolated encoder stage verifies unchanged compiled source/payload pins,
reuses the existing Venus/Codec2 binaries, and rebuilds the diagnostic native
probe plus the ranking XML. Shared stages and INTEG17 remain untouched.
Drivers, logs, return codes and verification scripts:
`/home/user/A9-build/mainline/integ18-host`.
The assembly driver hash is recorded in the manifest.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-18`.

- Sweep launcher: `run.sh`
- Fingerprint launcher: `run-fingerprint.sh`
- Images: `boot.img`, `vendor.img`
- Checksums: `SHA256SUMS`, `vendor.img.sha256`
- Records: `integrated-build.json`, `host-checks.json`, `source-handoff.json`

Both launchers use adjacent images/runtime/policy/compiler/assets.
`A9_BTRY_FORMAT` defaults to `0`; override `1` is honored and invalid values
are refused. Host launcher checks execute only an inert replacement b-try.

Build rc=0: kernel, image assembly, verification and finalization passed.
Host checks passed for generated and installed vendor XML against actual GSI
software AVC/HEVC declarations; the old rank256 XML fails the regression.
Startup/XML and SWEEP17 host tests passed. Image/source/runtime/assets hashes,
ABL, ext4, vendor init, all 101 shipped modules (zero CRC/vermagic failures),
unchanged kernel configuration/export CRCs, exact QSEE source pins, five
production listener SCM groups (native execution and ARM64 compilation),
five Android ARM64 FBE trace groups, diagnostics and both launchers passed.
Unchanged mounted-data handoff evidence is reused from INTEG17 after comparing
its helper and test source with that image's source commit. All eight entries
in `SHA256SUMS` were verified.

Image SHA256:

- boot.img: `9ed561c6992d82b233250a81d8b312fb667391d9301e7031f9b708cc1c2d7fb9`
- vendor.img: `0f53900dfb4b24a27145e89041a66d2efb08199c94775c304ab912a2a7e9c07f`
- Image.gz: `2e95813926d9d9b23de9ff33ebfbe5551a164118f3fff95ca0b8e6e54475d10c`

Host-only delivery; no adb, fastboot, EDL, USB or phone access and no hardware
qualification. The encoder hang remains unresolved; this delivery changes
selection priority until that fault is fixed.
