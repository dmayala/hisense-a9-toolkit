# ADSP D0 offline foundation

This directory compiles and audits a firmware-only ADSP DT variant. It installs
no module, copies no firmware, changes no default build, and starts no processor.
**PAS auto-boot is still enabled in the referenced kernel. Do not use this DTB
for a phone trial until a manual-start gate and the runtime harness are reviewed.**

`a9-adsp.dtsi` extends the integ-1 GPU/thermal/BT board DT. It changes exactly
five properties: ADSP status/firmware name, and APR/FastRPC/LPASS LPI status.
The latter three stay disabled in D0. The stock 30 MiB ADSP reservation and all
other reservations remain unchanged. APR and FastRPC are different consumers of
GLINK; D0 still needs the PAS/GLINK/sysmon transport dependencies.

Run from this checkout:

```sh
bash scripts/mainline/adsp/build-adsp-dtb.sh
python3 -I scripts/mainline/adsp/test-d0-dt.py <output>/base.dtb <output>/adsp.dtb
```

Inputs default to the existing local Linux 7.2.10 GPU tree, integ-1 objects and
`android/kernel-snap-gpu-thermal-bt` under `~/A9-build/mainline`. The script only
preprocesses/compiles DT and runs the reservation audit plus ABL emulation. ABL
uses a disposable zero-filled dummy kernel; its PASS does not prove bootability.
The script creates a fresh `dt-*` output directory under
`${A9_ADSP_OUT:-$HOME/A9-build/mainline/adsp}` and preserves previous results.
It refuses output outside `~/A9-build` and `/tmp`, and forbids the share and this checkout as
its explicit output. It uses the local Nix GCC with substitutes/builders off.

The DTB inherits a private board include. Keep all compiled/decompiled outputs
out of Git. Only source belongs here.

`check-d0-dt.py` compares every node and property, including symbols, and the FDT
reservation table. It accepts only the five exact changes above, verifies the
ADSP memory phandle, and requires CDSP/MPSS disabled. `test-d0-dt.py` exercises
the real compiled inputs and 14 rejecting cases. The separate existing
`dt-reserved-audit.py` verifies stock/baseline no-map coverage and non-overlap.

Missing-firmware auto-boot is **not** an implemented or approved defusing
method. The firmware class path is additive; fallback/cache paths and the
asynchronous remoteproc callback defeat a simple "offline then copy" rule.
The opt-in kernel patch described below supplies a probe gate. A runtime harness
must still verify `recovery=disabled`, `coredump=disabled`, exact tuple/firmware
hashes, and one explicit start per boot. The coordinator owns builds, review
admission, and all phone access.

The measured D0–D3 plan and remaining codec/HAL/player blockers are recorded in
`/home/user/A9/runs/adsp-plan.md` (local run report, not a deployable procedure).

## Opt-in PAS manual-start patch

`patches/0001-remoteproc-qcom-a9-adsp-manual-start.patch` changes only the PAS
driver. It is deliberately outside the default kernel patch series. Apply it
only in a private reviewed build; the DT compilation script does not apply it.

The read-only, default-false module parameter `a9_adsp_manual_start` applies
only when the root compatible is `hisense,hlte556n` and the PAS node compatible
is `qcom,sm6115-adsp-pas`. A targeted early-boot or non-PAS-1 descriptor refuses
probe. Immediately after allocation, before IRQ registration or publication,
the gate sets `auto_boot=false`, `recovery_disabled=true`, and coredumps disabled.
Other nodes/boards and parameter-off retain their descriptor behavior.

With the patch applied, a future reviewed harness must request
`a9_adsp_manual_start=1` when loading PAS (or the prefixed kernel command-line
parameter if built in), then verify the parameter and unique probe log plus the
ADSP's offline/recovery/coredump sysfs state. No asynchronous firmware request
is enqueued by gated `rproc_add()`, even if firmware is already available.
This suppresses probe auto-start; it does not forbid privileged sysfs/character
device starts, recovery commands or an independent in-kernel consumer. It does
not assert that bootloader firmware is physically stopped. Exclude those paths
and keep APR/FastRPC/LPI off until a separately reviewed stage admits them.

Run the host/source regression (GCC and patch must be available):

```sh
nix-shell --option substitute false --option builders '' --option substituters '' \
  -p gcc --run 'python3 -I scripts/mainline/adsp/test-manual-start.py'
```

The test patches only a temporary copy, extracts the production gate and exact
remoteproc registration/auto-boot functions, and executes them with host stubs.
It tests the scope/descriptor/default-state matrix, observes zero queued firmware
callbacks/attach work in gated registration, and checks that containment is set
at publication. Deliberate helper/core/order/parameter mutations must fail. This
is not a kernel boot, full PAS probe test, or runtime-harness rehearsal.

Implementation/compile provenance and indirect-start audit:
`/home/user/A9/runs/adsp-manual-start.md`. Independent review is still required;
there is no phone-trial GO and no bundled loader or start script.
