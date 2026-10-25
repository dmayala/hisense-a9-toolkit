# INTEG16 host delivery — 2026-10-24

One integrated boot/vendor pair from main `7f5aa3b69bf0f990a6d6ebad30952041eff99fbc`. Main was
fetched/re-checked before the kernel build and again immediately before image
assembly; timestamps are in `integrated-build.json`. Includes INTEG15
`5f12149`, encoder commits `041cc50`/`575eddb`/`79d1928`/`4ee369e`, and the
automatic dual-codec sweep `7f5aa3b`. No newer main commits existed at assembly.
Host-only build; no phone operation or hardware qualification.

Bundled launchers:

- Sweep: `/home/user/A9-build/mainline/integ-20261024-16/run.sh`
- Fingerprint: `/home/user/A9-build/mainline/integ-20261024-16/run-fingerprint.sh`

Both use adjacent boot/vendor, runtime, policy/compiler and assets. Runtime
inputs do not depend on previous build directories. The existing host registry,
USB-lock environment and installed host tools remain configuration. Formatting
defaults to `A9_BTRY_FORMAT=0`; override1 is honored, invalid values refused.

Sweep row18 now runs exact-name AVC and HEVC synthetic native probes, separately
bounded at30seconds. Each must report30inputs, nonempty encoded output and EOS
with success status. Missing hardware fails this required parity row. Only
numeric results are retained; ordinary-shell staging avoids a vendor execution
policy exception. Row17 still requires hardware decoding/drawing60/60 and EOS
under verified mute. RPMB/QSEE/FBE, saved-boot, qseecomd and stability evidence
capture from INTEG15 is retained, with the RPMB read-only guard unchanged.

Host checks pass: native kernel build/link with identical config/export CRCs;
all101 shipped modules with zero CRC/vermagic failures; ABL, ext4 and
vendor init; pinned encoder payload and full private library closure; identical
enforcing policy to INTEG15; image/source/runtime/assets hashes; five fake sweep
groups including AVC-pass/HEVC-fail rejection; five actual Android ARM64 FBE
trace groups; both relocated launchers against inert b-try for default0,
override1 and invalid-value refusal. No device tool was executed by these tests.

Image SHA256:

- boot.img: `c66b892f7e026c715054faf3b3d95daa737534509d0b2cd4e7df7c2bf6f80d0a`
- vendor.img: `0b0e869bedd24e01d23a2b343ba2b99eb58bfc99b27131a5ed16e77c3186a86b`
- Image.gz: `daaba3b17b7288e0329afc3aaf07bc0f89876c2b2cc22956a2ad5f2db1c06dc0`

The artifact directory contains `integrated-build.json`, `host-checks.json`,
`source-handoff.json`, `SHA256SUMS`, `encoder-stage.json` and `encoder-probes/`.
Build logs and native objects are in `../integ16-host/`. Existing INTEG15 and
older delivered images were preserved. Hardware encoder success and wrapped FBE
remain pending the next owned sweep.
