# SM6115 SMP, frequency and idle proposals

2026-10-15. Offline work only; **no device transport or phone script was run**.
See [mainline-smp.md](../../../docs/mainline-smp.md) for stock evidence, limits
and the coordinator's staged test gates. Nothing here edits `kernel/`.

**Built and offline-validated:** A, B0, B1 and C1 are under
`~/A9-build/mainline/sol-smp/images/`, with hashes and full command lines in
`manifest.json`. All passed component/config/DT checks and offline ABL emulation.
None has been tested on the phone by this agent. Start with A.

The tested Android baseline is
`~/A9-build/mainline/android/out/a9-android-boot.img`, SHA256
`d719592576dcda102043490175e632428ed0185bee927628d1a114a401544520`.
Its kernel provenance is `bd6d5b239` plus the existing ashmem request patch.
Use this known baseline, not the diskless recovery ramdisk, for Android trials.

| Variant | Change from previous stage | Command-line CPU settings | Required gate |
|---|---|---|---|
| A | secondary startup only | `maxcpus=8 cpuidle.off=1 cpufreq.off=1` | eight online CPUs, IRQ activity, same boot for >=300 s |
| B0 | register hardware cpufreq, hold governor | `maxcpus=8 cpuidle.off=1 cpufreq.default_governor=userspace` | two valid policies, stock domains, hardware-derived frequency tables |
| B1 | permit normal governor requests | `maxcpus=8 cpuidle.off=1 cpufreq.default_governor=schedutil` | a demonstrated transition, stable early/late captures |
| C1 | PSCI CPU idle via OSI, cluster idle omitted | `maxcpus=8 cpufreq.default_governor=schedutil` | successful OSI-domain initialization, CPU idle entry/resume on all cores |

`cpuidle.off=1` still permits ordinary architectural WFI. A/B do not enable
PSCI CPU/cluster suspend. C1 is not a test of cluster collapse or s2ram.
An informational early `failed to set PC mode: -3` is not itself a stage-A/B
failure and is not evidence that OSI was successfully enabled in C1.

## Offline image preparation

All outputs must be outside the repository and backups. A/B preserve the
complete tested image byte-for-byte except its command-line fields. The
generator freezes a baseline copy and refuses to silently change it later.
It produces full command lines with unique CPU keys, retaining the beacon,
clock/domain/regulator guards, QTEE skip, panic and all Android parameters.
Do not append duplicate `maxcpus`/`cpufreq.off` keys via A9_CMDLINE_EXTRA.

```sh
S=$HOME/A9-build/mainline/sol-smp
python3 -I scripts/mainline/smp/variants.py \
  --base "$HOME/A9-build/mainline/android/out/a9-android-boot.img" \
  --out "$S/images"
```

Outputs: `images/a9-smp-A.img`, `a9-smp-B0.img`, `a9-smp-B1.img`, their SHA256
files, full `A.cmdline`/`B0.cmdline`/`B1.cmdline`/`C1.cmdline`, preserved
components, and `manifest.json`. These are local artifacts, not git files.
`C1.cmdline` alone cannot enable idle in the old kernel: its driver is off.

The build interfaces already support whole-string `A9_CMDLINE` (kernel image
builder) and `A9_ANDROID_CMDLINE` (Android mkramdisk). The generated command-line
files are the exact overrides for those interfaces. The image reheader method
above avoids invoking or changing the coordinator-owned builders and keeps the
tested Android ramdisk and UFS DTB. Do not replace it with a diskless image.

For the build owner, the exact whole-string stage-A overrides are:

```sh
export A9_CMDLINE="$(cat "$S/images/A.cmdline")"
export A9_ANDROID_CMDLINE="$A9_CMDLINE"
```

Use `B0.cmdline`, `B1.cmdline` or `C1.cmdline` at their later accepted stage.
C1 additionally needs its rebuilt kernel/config/DTB below. These exports are
integration instructions; this agent does not invoke the owner's builders.

## C1 private build and DT

Only boot C1 after the coordinator accepts A/B results. Preparing C1
offline does not establish firmware compatibility. `build-c1.sh` takes an
isolated source archive of `bd6d5b239` plus the ashmem request patch; extracts
the exact tested config from the frozen kernel; enables the two PSCI-idle
symbols; and builds in `sol-smp/build-C`, preserving kernel release
`7.2.10-a9+`. It reads the shared tree/object cache but writes only its own
source/output. It never calls `kernel/build.sh` or changes shared sources.

```sh
bash scripts/mainline/smp/build-c1.sh
```

On this host, the completed C1 build uses the optional copied-cache path:

```sh
A9_SMP_CACHE_NAMESPACE=1 J=16 bash scripts/mainline/smp/build-c1.sh
```

It compares source contents, preserves timestamps only for identical files,
and makes differing pinned files newer. A private unprivileged mount namespace
then exposes the snapshot read-only at the original source path, allowing
normal Kbuild cache checks. It writes `build-C-cached`, not the shared output.
It neither changes cached `.cmd` metadata nor changes the parent namespace's
source/mounts. Hosts without user namespaces can use the default full rebuild.

The source/config pin deliberately excludes newer rail/display changes from
the first idle experiment. Record source, patch and config provenance when
changing the tested baseline. A9_SMP_SOURCE_COMMIT can select another source,
but the baseline hash assertion must also be reviewed for such a migration.

`0001-stage-C1-cpu-only-OSI-DTS-PROPOSAL.patch` adds `a9-smp-c1.dtsi` to a
private Linux copy. Include it **last** in the private Android board DTS.
It removes the two cluster-domain idle-state lists while retaining CPU domains
and the upstream `0x40000003` CPU state (low mode 3 matches stock).
`stage-c1.config` must be merged
after the coordinator's fragments. No PSCI return is forged or suppressed.

For the existing tested DTB, the equivalent offline transformation is:

```sh
cp "$S/images/components/board.dtb" "$S/kernel-C1/board-C1.dtb"
nix-shell scripts/mainline/kernel/shell.nix --run \
  "fdtput -d '$S/kernel-C1/board-C1.dtb' /psci/power-domain-cpu-cluster0 domain-idle-states"
nix-shell scripts/mainline/kernel/shell.nix --run \
  "fdtput -d '$S/kernel-C1/board-C1.dtb' /psci/power-domain-cpu-cluster1 domain-idle-states"
python3 -I scripts/mainline/smp/variants.py \
  --base "$S/images/baseline.img" --out "$S/images" \
  --c-kernel "$S/kernel-C1/Image.gz" --c-config "$S/kernel-C1/.config" \
  --c-dtb "$S/kernel-C1/board-C1.dtb"
```

The resulting `a9-smp-C1.img` retains the tested ramdisk, overlay and header
geometry. It carries a rebuilt kernel and a CPU-only-idle DT. Keep the matching
`images/components/recovery_dtbo.img` available to the coordinator.

## Validation and captures

Offline checks (the ABL script is a local emulator, with no phone access):

```sh
python3 -B -I scripts/mainline/smp/tests.py
python3 -B -I scripts/mainline/smp/validate-images.py "$S/images"
bash scripts/abl-check.sh "$S/images/a9-smp-A.img" boot \
  --dtbo "$S/images/components/recovery_dtbo.img" --quiet
```

Repeat the ABL check for each packed variant. Check runtime Image image_size
against the stock-derived 36,024,320-byte ceiling (inferred load-size limit), image size <=96 MiB,
unchanged ramdisk/DTBO hashes, and the manifest before any coordinator boot.
An ABL pass validates image packaging, not SMP, DVFS or idle behavior.

`0002-capture-cpu-telemetry-PROPOSAL.patch` is an **unapplied A9-project patch**
for the owner of `kernel/initramfs/a9-ctl`. It adds read-only CPU/sysfs fields
to the existing capture. No second transport or reboot path is introduced.
`0003-android-capture-cpu-telemetry-PROPOSAL.patch` adds matching fields,
interrupts and uptime to the Android vendor NCM collector, also unapplied.
`capture-addition.sh` is that insertion as reviewable text; this agent never
runs it. The existing capture does not yet include all CPU fields, so its old
one-core results cannot pass the new gate.

```sh
git apply --check scripts/mainline/smp/0002-capture-cpu-telemetry-PROPOSAL.patch
git apply --check scripts/mainline/smp/0003-android-capture-cpu-telemetry-PROPOSAL.patch
python3 -I scripts/mainline/smp/check-capture.py A /path/to/early /path/to/late
```

Use B0/B1/C1 as the first argument for their gates. A missing field, same-boot
interval <300 s, absent transition/idle coverage or unsupported firmware is
FAIL/INCOMPLETE, not a guessed pass. Early and late captures must preserve raw
boot-id/cmdline privately. B/C also require the seven upstream CPU TSENS zones
below the conservative 80 C test ceiling; keep watching temperatures between
these two point observations. Never commit captures. Continue only with
coordinator measurements. See the document for exact read-only observations and remaining
thermal/early-reset limits. No flashing or phone command is run here.
