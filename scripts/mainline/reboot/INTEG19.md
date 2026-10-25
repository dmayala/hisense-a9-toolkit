# INTEG19 host delivery — 2026-10-24

Built from current main `72a365d97132e5e0adb9b95c672125f8991967e9`.
Main was checked before the final kernel build at
`2026-10-24T15:46:13.218622+00:00` and immediately before assembly at
`2026-10-24T15:47:54.689624+00:00`; both selected that commit.
The earlier source was superseded before any image assembly.

Includes row29 logger identity `0c7e9ea`, display firmware assets `29136ac`,
MTP-before-encoder ordering `1fa8f14`, encoder tracing `d9687e1`/`3397692`,
stability `8802f17`, and bounded camera setup/partial evidence `da1422d`.
The hardware encoder hang remains unresolved; software encoder priority and
exact-name diagnostic hardware probes are preserved.

Uses the INTEG18 recipe and launchers: `build14-kernel.py`, fresh FBE trace,
fresh acceptance assets, and `build14.py` generation 19 with the INTEG16 and
subsequent required commits. INTEG17 supplies the immutable binary baseline.
The kernel and logger were freshly built. `stage-encoder-trace.py` assembled
the traced Venus/Codec2 payload after checking current patches against the
compiled sources, hashes, imports, and three Android QEMU helper groups;
the diagnostic probe was freshly compiled. Runtime sources were refreshed
from the selected main commit. No INTEG18 artifact or sweep tree was touched.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-19`.
Drivers, logs, return codes, archive receipts, and verification scripts:
`/home/user/A9-build/mainline/integ19-host`.

- Sweep launcher: `run.sh`
- Fingerprint launcher: `run-fingerprint.sh`
- Images: `boot.img`, `vendor.img`
- Checksums: `SHA256SUMS`, `vendor.img.sha256`
- Records: `integrated-build.json`, `host-checks.json`, `source-handoff.json`

Both launchers use adjacent images/runtime/policy/compiler/assets.
`A9_BTRY_FORMAT` defaults to `0`; override `1` is honored; invalid values
are refused. Launcher verification used an inert replacement b-try only.

Build rc=0: kernel, FBE, listener, encoder stage/rank, acceptance assets,
focused source tests, assembly, final verification, and finalization passed.
Checks cover installed encoder payload hashes, retained display firmware,
Android-shell logger identity/crypto lookup, MTP ordering, early statistics,
software encoder precedence, real ramdisk logger, RPMB guard, source/runtime
hashes, ABL, ext4, vendor init, and all 101 modules with zero CRC/vermagic
failures. Kernel configuration and export CRCs match the immutable baseline.
Unchanged INTEG17 mounted-data handoff evidence is explicitly reused after
comparing its helper/test source. All eight `SHA256SUMS` entries verified.

Image SHA256:

- boot.img: `f9913f116ebbc22b32974004f7db2e1521e9adcd8acb5f82d0496039c3c2787b`
- vendor.img: `ef5319dd7b9270d2bfab077b55014c754df0953c7d8824aa6ad616a9c7295407`
- Image.gz: `943ca26f80d32a5159d2aa524fc9eb43fd37b1d6f1a26a95a6b3340ccc707385`

To make build space, INTEG13 and INTEG14 were copied to
`$A9_DATA/archive/mainline-images/integ-20261024-{13,14}`.
Every file hash/symlink and unchanged source was checked before local removal.
Archive receipts retain original modes because NFS permissions are not
preserved. Approximately 18 GiB remained free after assembly.

Host-only delivery: no adb, fastboot, EDL, USB, or phone access; no hardware
qualification or phone attempt. The coordinator retains the live phone session.
