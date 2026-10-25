# INTEG17 host delivery — 2026-10-24

Integrated boot/vendor pair built from current main
`1f7783e9b625ce4cdf733c0a9021c0ade271aa8f`. Main was fetched and rechecked
before the kernel build and again immediately before assembly. Both checks
selected that commit; timestamps are in `integrated-build.json` and
`../integ17-host/build-start.json`.
The acceptance reorder `37e1e91` landed at 13:55:04Z, after assembly started
at 13:54:38Z, and is outside this image's source freeze.

Includes `9694e51` (stock SM6115 QSEE listener registration/continuation ABI),
`1f7783e` (successful FBE mount completes before init-owned ADB setup), and
`afb2afe` (b-try waits for reboot transport departure). Uses the INTEG16
recipe: `build14-kernel.py`, fresh FBE trace payload, and `build14.py` with
generation 17, INTEG16 requirements and the three required fixes. The assembly
driver is retained as `../integ17-host/assemble.py` with its hash in the manifest.
INTEG16 supplies the immutable binary baseline; the kernel is freshly linked
and bundled runtime sources are refreshed from the selected main commit.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-17`.

- Sweep launcher: `run.sh`
- Fingerprint launcher: `run-fingerprint.sh`
- Images: `boot.img`, `vendor.img`
- Checksums: `SHA256SUMS`, `vendor.img.sha256`
- Records: `integrated-build.json`, `host-checks.json`, `source-handoff.json`

Both launchers use adjacent images, runtime, policy/compiler and assets.
`A9_BTRY_FORMAT` defaults to `0`; override `1` is honored and invalid values
are refused. The existing AVC/HEVC encoder sweep, 60-frame decoder check,
RPMB read-only guard and QSEE/FBE/stability evidence capture are retained.

Build rc=0: kernel and image assembly both succeeded. Host verification passed:
image/source/runtime/assets hashes; ABL, ext4 and vendor init; 101 shipped
modules with zero CRC/vermagic failures; unchanged kernel configuration and
export CRCs; exact QSEE source pins; five listener ABI fixture groups compiled
and run natively plus ARM64 compilation; five Android ARM64 FBE trace groups;
five Android mksh mounted-data handoff tests; both relocated launchers against
inert b-try for default 0, override 1 and invalid-value refusal. All eight
entries in `SHA256SUMS` were verified. Logs, return codes, verification scripts
and kernel objects are in `/home/user/A9-build/mainline/integ17-host`.

Image SHA256:

- boot.img: `7bf1eaa4a5ce21f08427a5c84e9eac0fa7976ef48a3c1014f0d642386f40a089`
- vendor.img: `1efa5b33b48f3b8c0a9740547e44581db0479c299dccf37e4e2f69555f9de426`
- Image.gz: `76a9435a573ed569751795ebfa59a10c47298e91fd44695bb0e9b9f0056c3a32`

Host-only delivery; no phone access or hardware qualification. The live sweep
worktree and its output directories were untouched.
