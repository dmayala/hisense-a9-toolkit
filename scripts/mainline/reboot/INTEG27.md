# INTEG27 host delivery

Frozen main commit: `eda8a0d48380ad3baf52d8b964274f60ef612db7`.
RPMB guard commit: `17c12a971b7a562178b742ad6a0fad3fb5016df1` (17c12a9), with 30995fd diagnostics.
Frozen worktree: `/home/user/A9-wt/integ27`.
Main observed before assembly: `46fbb415a45131d2b3566204258a2083dd78ea89`.
The user explicitly froze source before compilation; later release-profile commits are excluded.

| Image | Bytes | SHA256 |
|---|---:|---|
| /home/user/A9-build/mainline/integ-20261024-27/boot.img | 14008320 | `c8f9f40d342df937e76f251cf0ff41a8a5ef175e08aa856805c9574eaa5e8476` |
| /home/user/A9-build/mainline/integ-20261024-27/vendor.img | 847532032 | `31d661ce292a862a9c96c2a1a7ffee9a787fe2e4174f03d050ef8bc9a4af9ae5` |

Build rc=0; images exist; SHA256SUMS verified. Host only; no flash or phone access.
Includes 17c12a9/30995fd RPMB bootstrap and diagnostics, eda8a0d drain, 7d232e9 CPU-readable encoder input default, 880da6d/57544b1 one-boot encoder variants, 50d9ee2 stock E Ink PG, and48bc99e headphone jack.
The kernel, a9-rpmb-diag, battery/E Ink/AF/torch modules and acceptance assets were built for this candidate. Compiled Venus and Codec2 inputs were replayed against frozen patches and checked against target exports.
FORMAT defaults to0 and honors overrides. Physical persist remains read-only.

The opt-in drain stage measures 3600 seconds with screen off and Wi-Fi connected. This image gives the existing Wi-Fi owner a 7200-second bound within its supported 600..14400 range; all other configuration and metadata are retained. Charging restoration uses the parent finally block, independent host watchdog and kernel error/floor/70-minute bound. Start must exceed 35%; restore triggers at 35% to leave margin above 30%. Missing hardware sample coverage fails the row. Physical current calibration, USB input suspend and battery-floor behavior remain unqualified.

`run-hvenc-variants.sh` enables four attempts in one boot, using fresh codec processes and separate evidence directories. CPU_READ defaults on. Mapped EOS is on for each case; output-size/work-mode pairs are 1/1,1/0,0/1,0/0. Finally attempts reset all three kernel flags to off. `run.sh` keeps these experimental flags off; `run-mapped-eos.sh` is also retained for explicit EOS-only comparisons. A passing native encode and validated MP4 are required for row 18 success.

The following command is for the coordinator. It starts a formatted slot-B sweep and can occupy the phone for up to two hours. The launcher and actual hook received FORMAT/PIN/HVENC/variants/drain flags in inert host replay; no phone command was run here.
```sh
cd /home/user/A9-wt/integ27
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ27-f1-20261025 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=7200 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 A9_SWEEP_DRAIN=1 \
/home/user/A9-build/mainline/integ-20261024-27/run-hvenc-variants.sh \
> /home/user/A9-build/mainline/sweep-integ27-f1-20261025.out 2>&1
```

Use the same command with `run.sh` for the single CPU_READ-default qualification path. Keep HOLD=7200 when requesting drain. No explicit CPU_READ environment override is needed.
Host evidence: `/home/user/A9-build/mainline/integ27-host`; `host-checks.json` and `SHA256SUMS` accompany the images.

