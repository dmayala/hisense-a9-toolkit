# INTEG29 host delivery

Frozen main commit: `6821914bedf3d15540b373bde436a6b634b226d1`.
Frozen worktree: `/home/user/A9-wt/integ29`.
Main rechecked before assembly: `44a87ba2a9e798cb7b268a1ceb32e4f6b759329c`.
Later main commits do not replace the explicitly frozen source.

| Image | Bytes | SHA256 |
|---|---:|---|
| /home/user/A9-build/mainline/integ-20261024-29/boot.img | 14008320 | `9c9ada8883fe3de14a206be9e79398e590af70327ae7cbc0868a32a1549d4094` |
| /home/user/A9-build/mainline/integ-20261024-29/vendor.img | 847532032 | `85be145b7162c4472bc17aa9c8a86b5669e98f9a85794019dbe2b61dddc52d0e` |
| /home/user/A9-build/mainline/integ-20261024-29/system.img | 3569860608 | `ba6c0a9133cd270a22528b1c889cc07c0b632c0a1ec734e66e0c443f341ca57e` |

Build rc=0; files exist; SHA256SUMS verified. Host only; no flash or phone access.

Includes 5e5e140 USB/front-camera fixes, 9a42380 Venus reset/retry evidence, 47ac6ad/7711efa linear probe, 821e239 replacement-process framework timer fix, 45bd4ab Vulkan row, 5ee6d88 RPMB diagnostics and 5426b95 SYSTEM delivery. Routing fix 037f9b3 sends CPU_READ properties and drain framework commands through ordinary shell; native charging/module controls retain the confined nc path and tracked watchdog ownership.

Build used `A9_INTEG_SYSTEM=$A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img`: the original unpatched v8 GSI. Its SHA256 is `14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793`. The private SYSTEM copy contains the pinned framework repair and preserves the platform identity; raw jar readback, metadata, preopt removal, bytecode replay and e2fsck were checked. The initial Binder ANR remains unresolved; this fixes the replacement timer lifecycle.

Generated launchers select the built SYSTEM, and B-try uses its frozen compatibility/space/SHA checks and B-only SYSTEM delivery/readback. Refreshing boot/vendor alone would omit the framework fix. FORMAT defaults to 0 and honors overrides. Physical persist remains read-only.

Kernel, bridge/RPMB diagnostics, Venus, E Ink, battery, AF/torch and acceptance assets were rebuilt; Codec2 inputs were replay-verified against frozen sources. Final 103-module ABI, both vendor HAL closure gates, Android linking, Gatekeeper package groups, ABL/init/ext4 and source/runtime hashes passed. Source tests cover the new USB/UI-mode/Vulkan/linear paths; inert launchers verify SYSTEM plus FORMAT/PIN/HVENC/drain/registration flags.

The default command makes one surface qualification with CPU_READ=1, mapped EOS=Y and the boot-default bidirectional=N. It does not automatically retry bidirectional=Y after a possible reset; 9a42380 manual retry isolation/evidence helpers are bundled. The combined hook observes fingerprint registration/chip initialization without enrollment or authentication, then runs the independent acceptance rows. The earlier multi-case launcher is retained as an explicit experimental option, not selected below.

The optional freshly compiled linear bundle is in `linear-probe/`. Set A9_SWEEP_HVENC_LINEAR=1 below, or select run-linear-prestep.sh, to run its single mmap attempt before registration/acceptance. It retains live kernel/logcat evidence; transport loss ends the hook without a follow-on attempt. Default 0 leaves the pre-step off. All host-only outer-hook overrides and qualification source pins are recorded in integrated-build.json.

Drain is opt-in, fixed 60 minutes, with screen off and connected Wi-Fi, suspend-safe counter/clock sampling, a 35% restoration margin, parent finally, independent host watchdog and kernel restoration bound. The existing Wi-Fi owner bound is 7200 seconds. Charging calibration and SYSTEM/codec behavior still require the owned device run.

The coordinator command (not executed here):

```sh
cd /home/user/A9-wt/integ29
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ29-f1-20261025 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=7200 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 A9_SWEEP_HVENC_CPU_READ=1 \
A9_SWEEP_HVENC_VARIANTS=0 A9_SWEEP_DRAIN=1 \
A9_SWEEP_HVENC_LINEAR=0 \
/home/user/A9-build/mainline/integ-20261024-29/run-mapped-eos.sh \
> /home/user/A9-build/mainline/sweep-integ29-f1-20261025.out 2>&1
```

Older integ19–26 images/sweeps were copied to NFS, fully hash-verified and removed locally. Receipt: `$A9_DATA/mainline-archive/integ29-space-20261025/receipt.json`. Integ27/28, their sweeps and active-agent data were preserved.

Host evidence: `/home/user/A9-build/mainline/integ29-host`; image evidence: `integrated-build.json`, `source-handoff.json`, `host-checks.json` and `SHA256SUMS` alongside the images.

