# INTEG29 preparation — not frozen or built

Main checked at `5426b9585008908ab91d09fc8a9f68bc0dadb285`. Required source is merged:

- `821e239`: replacement-process phone startup timer fix; the initial Binder ANR remains unresolved.
- `45bd4ab`: Vulkan acceptance row/probe; build fresh acceptance assets.
- `5ee6d88`: RPMB diagnostics distinguishing full RR cache from outside-map denial; rebuild the diagnostic helper.
- `5426b95`: SYSTEM delivery pipeline, including A9_INTEG_SYSTEM/--integ-system, private patched system.img plus hashes, frozen check helpers, launcher selection and slot-B delivery/readback.

Configure `A9_INTEG_SYSTEM=$A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img` using the original unpatched v8 GSI. Deliver the resulting SYSTEM together with boot/vendor; a vendor-only update cannot apply the framework fix. Existing host transport/packaging tests passed as recorded in `runs/integ-system-delivery-20261025.md`; no new test/build or phone action was run for this preparation.

Remaining: explicit GO, disk headroom, then freeze current main and build/verify all three images. Current free space is 20034953216 bytes. Even just the original SYSTEM-sized copy plus the last boot/vendor sizes and the 15 GiB reserve require 20537528320 bytes: short by at least 502575104 bytes, before compiler/asset scratch space. Recheck and archive older unused outputs before building; integ28/sweep artifacts remain untouched.

Preparation: `/home/user/A9-build/mainline/integ29-host/preparation.json`. No integ29 worktree or image directory was created. The previous integ28 recipe remains a reference; adapt its verification/record and combined launcher tests to the newly delivered SYSTEM at GO. The two-hour sweep and any additional cases must be selected from the current owned-run evidence, not copied blindly.

Worktree hygiene: retired merged/clean integ26 source, previous agent-phoneanr and vulkan28 worktrees, and the two merged branches. Verified snapshots/ignored-file archives are in `$A9_DATA/mainline-archive/worktree-patches-20261025/integ29-hygiene/`. Frozen integ27/28 and active agent worktrees were preserved. Eight worktrees remain at this audit; archive manifest records the exact list.
