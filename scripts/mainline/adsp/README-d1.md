# ADSP D1: sound card enumerates (no output), offline preparation

D1 = D0 (one authenticated ADSP start, bounded stop) **plus** the Q6 audio services
and one ASoC card that has no codec, no back end and no amplifier. Nothing here is a
phone-run authorization: D1 needs its own independent review of the exact frozen
tuple, and a passed D0 (receipt must cite it, `d1-host.py` refuses otherwise).
No audio is played, no PCM or mixer node is opened, no pin/clock/regulator/GPIO is
requested by the card.

## What D1 adds on top of D0

| Layer | D0 | D1 |
|---|---|---|
| DT (`a9-adsp-d1.dtsi`) | ADSP on, APR/FastRPC/LPI disabled | ADSP on, **APR enabled as upstream** (apr-v2, channel `apr_audio_svc`, services q6core@3 q6afe@4 q6asm@7 q6adm@8, PD `avs/audio`->`msm/adsp/audio_pd`), FastRPC and LPASS LPI disabled, new `/sound` node |
| `/sound` | none | `qcom,qrb4210-rb2-sndcard`, model `A9D1NoOutput`, links MultiMedia1/2 (q6asm-dai front ends only; dummy codec; **no** back-end link, no pinctrl/clock/gpio/regulator/routing) |
| Config (`d1.config`) | APR, SOUND off | `QCOM_APR`, `SOUND`, `SND`, `SND_SOC`, `SND_SOC_QDSP6`, `SND_SOC_SM8250`, `SOUNDWIRE` modular; SoundWire controller, LPI, LPASS macros, WCD/WSA, FastRPC off |
| Sidecar order (`modules.d1`) | 12 modules, PAS last | audio modules first (`apr`, q6 stack, `snd-soc-sm8250` + their closure), then the D0 chain, PAS last |
| Target (`d1-target.sh`) | ADSP up + GLINK/rpmsg/pd-mapper ready, 180 s | same, then APR services (<=40 s), card (<=20 s), 60 s hold with the card, stop, 10 s offline |

`diff d0-target.sh d1-target.sh` shows every harness change. Unchanged: ledger,
guardian/dead-man, writers, tuple/live-hash checks, E Ink/thermal gates, D0 fault regex
(byte-identical; D1 tokens are appended).

## What the harness verifies (all reads of /proc and /sys)

* before: no `/proc/asound`, no `aprbus`, PAS absent, loaded modules == baseline, kernel
  autoload helper empty;
* after start: D0 readiness (qrtr-smd rpmsg + one pd-mapper aux device under the ADSP);
  then exactly one `apr_audio_svc` rpmsg channel under `a400000.remoteproc` bound to
  `qcom,apr`, and exactly the four `aprsvc:service:4:{3,4,7,8}` devices bound to
  `qcom-q6core/q6afe/q6asm/q6adm` (any other service = stop);
* card: exactly `card0`, id `A9D1NoOutput`, `/proc/asound/pcm` has exactly two lines
  (MultiMedia1, MultiMedia2), `/sys/class/sound` holds only card0/controlC0/timer/
  pcmC0D{0,1}{p,c}, all four substreams `closed`;
* throughout: loaded modules subset of baseline + `modules.d1` (a codec/macro/SoundWire
  controller/LPI/ESS module = stop), no SoundWire device, no `a7c0000` device, no process
  holding any `/dev/snd/*` node (periodic observation, every ~5 s, per-process fd directory must be
  read successfully or the run stops; it cannot exclude a transient open between samples), plus every D0 gate (kernel fault matcher incl. SMMU
  fault/PDR/APR/ASoC tokens, thermal, E Ink gates, boot id);
* after stop: ADSP offline for 10 s, no restart; card/APR presence recorded
  (`D1-AFTER-STOP card=gone|present`), informational only.

Timing: loads <=8 s each, start 12 s, rpmsg 10 s, APR 40 s, card 20 s, hold 60 s,
stop 12 s, offline 10 s; dead-man 300 s; host 340 s.

## Measured vs inferred (read this before review)

* **Measured (D0 run.log, attempt 5):** ADSP authenticates, `remote processor adsp is
  now up`, runs 180 s, stops offline; the full marked window is clean under the D1
  matcher (`tests/mainline-adsp/test-d1-matcher.py --replay`). The D0 kernel log at
  default level contains NO GLINK/rpmsg/pd-mapper lines: D0 readiness was proven through
  sysfs only (`ready()`), so there is no measured APR/PDR service line to quote.
* **Inferred (source, kernel 7.2.10):** the APR/service/device names and driver names
  above (`drivers/soc/qcom/apr.c`, `sound/soc/qcom/qdsp6/*`, upstream `sm6115.dtsi`),
  the ALSA card id, the PCM names, and that the stock firmware's audio PD comes up for
  this tree (`ADSPUA.JSN` gives instance 74 / `msm/adsp/audio_pd`, which the kernel
  pd-mapper also serves). If any name differs the first run fails closed.
* **Unmeasured risks:** (1) `q6asm dais` has `iommus = <&apps_smmu 0x1c1 0>`: Linux
  attaches that stream ID at probe; an ADSP DMA with that SID would fault (the matcher
  stops the run on any SMMU fault). (2) The unregister path when the ADSP stops under
  a registered card is untested hardware behaviour. (3) q6core/AFE/ASM probes talk to the
  live DSP, but never open an AFE port or ASM session.

## Reproduce offline (no phone)

```sh
cd <adsp worktree>
# DT: builds integ-1 + D1 DTB, exact-delta check, 41 mutation checks, reserved-memory
# audit, ABL emulation (boot + recovery). Needs only dtc/mkbootimg already used by D0.
bash scripts/mainline/adsp/build-d1-dtb.sh
# Kernel config resolve (kconfig only, ~100 MB):
nix-shell --option substitute false --option builders '' --option substituters '' \
  scripts/mainline/kernel/shell.nix --run "bash scripts/mainline/adsp/check-d1-config.sh"
# Harness rehearsals (fake adb; the phone's own mksh/toybox under qemu-user):
python3 -I tests/mainline-adsp/test-d1-host.py
python3 -I tests/mainline-adsp/test-d1-transport.py --sysroot "$A9_PHONE_SYSROOT" --qemu "$A9_QEMU"
python3 -I tests/mainline-adsp/test-d1-phone.py --sysroot "$A9_PHONE_SYSROOT" --qemu "$A9_QEMU"
python3 -I tests/mainline-adsp/test-d1-matcher.py --sysroot "$A9_PHONE_SYSROOT" --qemu "$A9_QEMU" \
  --replay <D0 run.log>
python3 -I tests/mainline-adsp/test-d1-services.py
```

## Image build (not done by the author: / had 9.1 GB free, the recipe needs >= 12 GB)

Same recipe as D0 (`README-d0.md`) with the D1 scripts; every output is a new direct
child of `~/A9-build/mainline/adsp`:

```sh
OUT=$HOME/A9-build/mainline/adsp/d1-<date>
nix-shell ... scripts/mainline/kernel/shell.nix --run "python3 -I scripts/mainline/adsp/build-d1.py '$OUT' --jobs 8"
python3 -I scripts/mainline/adsp/stage-firmware.py "$OUT/firmware/qcom/sm6115/hisense/hlte556n" --mcopy <mcopy>
nix-shell ... --run "python3 -I scripts/mainline/adsp/freeze-d1.py '$OUT'"
python3 -I scripts/mainline/adsp/verify-packaged.py "$OUT"
# system is unchanged v7: audit the D0 extraction read-only instead of re-extracting 3.2 GB
python3 -I scripts/mainline/adsp/audit-services.py --system <D0 OUT>/audit/system-image \
  --vendor "$OUT/android/work/vendor-stage-bt" --ramdisk "$OUT/android/work/ramdisk/root" --output "$OUT/audit/final"
# dispositions.json: hash-bound, reviewed per finding (the D0 file is the starting point;
# any new finding hash is NO-GO until reviewed)
python3 -I scripts/mainline/adsp/admit-services-d1.py --audit "$OUT/audit/final/services.json" \
  --dispositions "$OUT/dispositions.json" --snapshot "$OUT/snapshot" --output "$OUT/service-admission.json"
nix-shell ... --run "python3 -I scripts/mainline/adsp/make-bundle-d1.py '$OUT' --services '$OUT/audit/final/services.json'"
```

Free space needed first: either remove `d0-20261017/audit/system-image` (3.2 GB, re-creatable by
`debugfs rdump`, D0's `services.json` evidence stays) or archive the D0 `linux`/`build` trees
(2.1 GB each) to a non-`$A9_DATA` disk. Expected D1 footprint is about 6 GB (the arm64
defconfig builds many extra codec modules once sound is on; none is ever staged).
Both D1 admission checks run in the freeze/admit steps: the staged module closure is
refused if it contains any codec/macro/SoundWire-controller/LPI/ESS/PA/q6apm/USB module
name, and the service admission refuses any audio module in boot/system/vendor or autoload.

Review 72 fixes: live manifest carries D0's attempt-3 corrections (three bind-masked permission files expect the
empty-file hash, `memory@80000000/reg` omitted; `tests/mainline-adsp/test-d1-live.py` checks the bundle against the D0 fix4 list),
fd scan fails closed, GLINK matcher covers the real `qcom_glink_native.c` messages. The closure has 33 modules.

Receipt for `d1-host.py` (private, never committed): as D0's plus `"stage": "D1"`,
`"independent_review": "ADMITTED-ATTENDED-D1"` and `"d0_pass_evidence"` quoting the D0
`PASS-ATTENDED-D0` result and its evidence path (>= 40 characters).
