# INTEG20 host delivery — 2026-10-24

Built from main `9c48b6f3df74d1062c54a432d4f18da02cf4523b`. Main was checked before the kernel
build at `2026-10-24T16:30:55.353157+00:00` and immediately before assembly at
`2026-10-24T16:33:45.981675+00:00`; both selected that commit. The build
worktree remains frozen at that source. Later main changes are separate from
this image's recorded source identity.

Includes logger kmsg-follow `e57a00d`, stock QTI Gatekeeper `e68d192`/`28fbc74`,
PIN lifecycle `22f0080`, Wi-Fi/rear-video diagnostics `79177af`, RPMB evidence
`a79c668`, and explicit PIN opt-in forwarding `9c48b6f`. No later camera work
was merged at the pre-assembly main check. The stock service and implementation
are installed with one HIDL provider and enforcing policy; the nonsecure
Gatekeeper APEX and its selection property are removed.

Uses the INTEG19 recipe: fresh `build14-kernel.py`, FBE trace, traced encoder
stage and logger, plus `build14.py` generation 20 with required ancestors.
INTEG17 remains the immutable binary baseline. Images and launchers from
INTEG19 and all sweep worktrees remain untouched. The compiled Venus/Codec2
tracing is retained; hardware encoder repair is unproven.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-20`.
Drivers, logs, return codes, archive receipts and verification scripts:
`/home/user/A9-build/mainline/integ20-host`.

- Sweep launcher: `run.sh`
- Fingerprint launcher: `run-fingerprint.sh`
- Images: `boot.img`, `vendor.img`
- Checksums: `SHA256SUMS`, `vendor.img.sha256`
- Records: `integrated-build.json`, `host-checks.json`, `source-handoff.json`

Both launchers use adjacent frozen images/runtime/policy/compiler/assets.
`A9_BTRY_FORMAT` defaults to `0`; override `1` works; invalid values refuse.
`run.sh` preserves `A9_SWEEP_PIN_LIFECYCLE` from the environment, including the
exact documented opt-in `fresh-disposable-formatted-b`. It defaults empty.
The existing PIN test's prerequisites and cleanup guards remain in place.
Launcher verification executes an inert replacement b-try only.

The supplied `/home/user/A9-build/mainline/fbe-pin-assets-20261024` bundle was
checked against current main. Its Probe.java pin predates required `79177af`,
so the complete assets were freshly rebuilt from the selected main source,
including the PIN implementation, rear-video diagnostics and retained display
firmware. The supplied bundle is unchanged; `assets-source.json` records its
receipt and the source-pin mismatch. Every installed asset/source pin matches
the frozen source. This fresh bundle is adjacent at `acceptance-assets/`.

Build rc=0: kernel, FBE/listener, encoder stage/rank, source tests, PIN tests,
logger policy-load replay, assets, assembly, Gatekeeper checks, final image
verification and finalization passed. Gatekeeper's five groups check unique
provider/labels, 1,339 unrelated files against the immutable baseline after
excluding recorded integration replacements, its 21-object recursive dependency
closure and strong imports, policy/init, ext4 and no image growth.

The real boot ramdisk contains the kmsg-follow logger, and the installed vendor
contains its companion tmpfs permissions. The four simulated policy-load groups
use production logger helpers with compiled TE/MLS permissions; only the host
replay omits the unsupported FunctionFS flag, while the full production policy
is separately compiled. Existing logger identity/regular-file regressions pass.
All 101 modules pass CRC/vermagic checks; kernel configuration and export CRCs
match the baseline. ABL, vendor init/ext4, source/runtime/policy/compiler/assets,
software encoder precedence and both launchers pass. Unchanged INTEG17 mounted
handoff evidence is explicitly reused after helper/test source comparison.
All eight `SHA256SUMS` entries verified.

Image SHA256:

- boot.img: `6461e412e90f62853cd389378eecac71f5b9e09214300a6c4871492304ef92f2`
- vendor.img: `bbbbc6234f237d1cb654e5d7e8d675f8e72f3fbdd89c5143a91a85a359b8287b`
- Image.gz: `58d9011133032688fd922657f76103438b6dab684d23474530c3796d3a83e03d`

INTEG15 and INTEG16 were copied to
`$A9_DATA/archive/mainline-images/integ-20261024-15` and `-16`.
Every file hash/symlink and unchanged source was verified before local removal.
Receipts retain original modes because NFS permissions are not preserved.
More than 15 GiB remained free after verification.

Host-only: no adb, fastboot, EDL, USB or phone access; no phone attempt or
hardware qualification. Credential success, logger persistence after a real
policy load, and hardware AVC/HEVC output/EOS remain for the phone owner.
