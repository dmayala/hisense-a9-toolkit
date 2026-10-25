# INTEG14 host delivery — 2026-10-24

One integrated boot/vendor pair, built from main source `7bc5d533187eccf0f68fce061ae88c02dcc50a0b`.
Includes EINKDIAG 99ecf42, FP14 120b4ff, same-key reopen aefbdc2, early radio/Play
and FBE13. Hardware qualification, real wrapped-FBE reopen and hang persistence
remain pending the owning run. Host-only preparation; no phone tools were run.

Launchers (all inputs adjacent):

- Sweep + initial/retained FBE evidence: `/home/user/A9-build/mainline/integ-20261024-14/run.sh`
- FP14 framework-sensor registration check: `/home/user/A9-build/mainline/integ-20261024-14/run-fingerprint.sh`
- Same-key stock-A TEE reopen control: `/home/user/A9-build/mainline/integ-20261024-14/run-reopen.sh`

The existing host registry/USB lock environment and installed host tools remain
configuration. Boot/vendor, frozen runtime, policy inputs/compiler, acceptance
assets, sleep logger and reopen client/library closure have no runtime input
from older build directories. Parent/cached sources are build-time inputs only.
Launchers were exercised with inert b-try/lock/entry fixtures; no device call.

Kernel and packaging:

- Native incremental link retains INTEG13 config and all 13,197 kernel export
  CRCs. Rebuilds the deadline worker with best-effort EMERG dump after its
  existing independent reset backstop is armed.
- Rebuilds MSM with published DPU fixes and A9-only boot/probe tracing.
  All 101 shipped modules / 6,243 imports match the new kernel/providers, zero
  CRC/version mismatches. Other 100 module binaries remain unchanged.
- Rebuilds the ramdisk logger for immediate critical-marker fsync. Every other
  ramdisk entry, including init, remains unchanged. Command-line fields are
  byte-identical to INTEG13; DT changes only ramoops max-reason 2→3, with its
  inherited reserved-memory layout unchanged. Deadline/reset controls retained.
- Refreshes vendor loader, enforcing policy including reopen input-read scope,
  and current bundled hooks. No speculative power/order/IRQ mitigation.

Validation: ABL/ext4/init PASS; image/source/runtime/assets pins PASS; bundled
policy relink reproduces packaged binary, 45 allow/deny checks, Wi-Fi ioctl
scope and 0 permissive domains PASS; frozen runtime vendor preparation PASS;
FP14 five local fixture groups PASS; native reopen synthetic/status/refusal/
redaction/cleanup/same-key mocked controls and bundled self-test PASS; three
inert launchers plus two reopen phase wrappers/four unowned hook refusals PASS;
FBE/ANR evidence privacy and Play account-prerequisite/failure controls PASS.

Image SHA256:

- boot.img: `279ec92f0948b27b89d58f3b4d9e30c3a0373d92344d70f0c14e77f6b98b7b3f`
- vendor.img: `ac894b4d9236df4cb2910e2d891725da3c9a5a2aeaa356103c9443ea7658d14d`
- Image.gz: `88f4c7962d7d27ecfad4f319cc9b5e3f8c1663f9e1178b3ac40ff9d87dd91d14`

Artifact directory includes integrated-build.json, SHA256SUMS, host-checks.json,
host-checks-extra.json, source-handoff.json, reopen-manifest.json and host-checks/
fixtures/logs. Private rebuild logs and native objects: ../integ14-host/.
Initial generic suspend packing refused primary-command-line overflow; the
final recipe preserves the already verified fields. A copied nonexecutable
QEMU linker caused an isolated test-fixture failure; corrected native tests
passed. Neither was a phone observation or a delivered runtime failure.
