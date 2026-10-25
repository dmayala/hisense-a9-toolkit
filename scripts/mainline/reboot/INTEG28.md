# INTEG28 host delivery

Frozen main commit: `1e8e81da45304dd1778f967be74b0cd659a271d7`.
RPMB guard commit: `17c12a971b7a562178b742ad6a0fad3fb5016df1` (17c12a9), with 30995fd diagnostics.
Frozen worktree: `/home/user/A9-wt/integ28`.
Main observed before assembly: `45bd4abcaa93792ddad5bb7cdd89f94ffd72ddb6`.
The user explicitly froze current main before compilation; the phone-ANR fix was not on main at freeze and is excluded.

| Image | Bytes | SHA256 |
|---|---:|---|
| /home/user/A9-build/mainline/integ-20261024-28/boot.img | 14008320 | `274e2643de99a923ca7a9eb7c0ef7af91ef38a56fa75132de620be875960150d` |
| /home/user/A9-build/mainline/integ-20261024-28/vendor.img | 847532032 | `a7f2fc58fedabe4dacc2c8e8e89b4705a7833b8e48c2bd4176b48a2e307eeaaf` |

Build rc=0; images exist; SHA256SUMS verified. Host only; no flash or phone access.
Includes f46d495 restart module-load fix, 3572e32 legacy telemetry invalidation, a399a9a fingerprint dumps off, b0839b2 E Ink-safe outer fingerprint hook, 11a3e34 harness corrections and 470f412 Venus watchdog recovery/bidirectional-buffer experiment, plus all earlier main features at freeze.
The kernel, a9-rpmb-diag, battery/E Ink/AF/torch modules and acceptance assets were built for this candidate. Venus modules were freshly compiled from all five frozen patches against target exports; Codec2 inputs were replayed against frozen patches.
FORMAT defaults to 0 and honors overrides. Physical persist remains read-only.

The opt-in drain stage measures 3600 seconds with screen off and Wi-Fi connected. This image gives the existing Wi-Fi owner a 7200-second bound within its supported 600..14400 range; all other configuration and metadata are retained. Charging restoration uses the parent finally block, independent host watchdog and kernel error/floor/70-minute bound. Start must exceed 35%; restore triggers at 35% to leave margin above 30%. Missing hardware sample coverage fails the row. Physical current calibration, USB input suspend and battery-floor behavior remain unqualified.

`run-hvenc-variants.sh` enables up to five attempts in one boot, using fresh codec processes and separate evidence directories. CPU_READ is explicitly on and mapped EOS is on for each case. First, bidirectional buffers N then Y with output-size/work-mode off, each through a fresh native codec process. Optional later output-size/work-mode pairs are 1/1,1/0,0/1 with bidirectional buffers off; they run only if the original owner deadline leaves room. Finally attempts reset all four kernel flags to off. The combined launcher observes fingerprint registration and chip initialization, without enrollment/authentication, before independently running all acceptance rows. `run.sh` keeps these experimental flags off; `run-mapped-eos.sh` is also retained for explicit EOS-only comparisons. A passing native encode and validated MP4 are required for row 18 success.

The new combined launcher and bidirectional N/Y scheduling are explicit host-only recipe overlays on the frozen runtime. Their source hashes and the refreshed qualification-manifest pin are recorded in integrated-build.json; the frozen worktree was not edited. All native payloads were built or replay-verified against the frozen source.

The following command is for the coordinator. It starts a formatted slot-B sweep and can occupy the phone for up to two hours. The launcher and actual hook received FORMAT/PIN/HVENC/variants/drain/CPU_READ flags in inert host replay, with independent fingerprint-registration observation; no phone command was run here.
```sh
cd /home/user/A9-wt/integ28
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ28-f1-20261025 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=7200 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 A9_SWEEP_HVENC_VARIANTS=1 \
A9_SWEEP_HVENC_CPU_READ=1 A9_SWEEP_DRAIN=1 \
/home/user/A9-build/mainline/integ-20261024-28/run-hvenc-variants.sh \
> /home/user/A9-build/mainline/sweep-integ28-f1-20261025.out 2>&1
```

Use run-hvenc-variants.sh for the requested combined sweep. It calls run-sweep.sh, whose selected hook explicitly fixes the fingerprint child to the registration observer; an external enrollment override is ignored. Keep HOLD=7200 when requesting drain. run.sh remains the ordinary acceptance-only launcher and defaults FORMAT=0.
Host evidence: `/home/user/A9-build/mainline/integ28-host`; `host-checks.json` and `SHA256SUMS` accompany the images.

