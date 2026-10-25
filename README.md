# hisense-a9-toolkit

Tooling, research notes and mainline-Linux/Android integration scripts for the
Hisense A9 (HLTE556N, Snapdragon 662 / SM6115) E Ink phone.

* `docs/` measured findings (partition layout, TWRP port, E Ink pipeline, mainline status).
* `scripts/` host scripts (backup, EDL, flashing, image patching) and `scripts/mainline/`
  (kernel patch series, Android vendor integration, per-subsystem bring-up).
* `tests/` offline tests that run against a fake phone.
* `.claude/skills/` agent-oriented runbooks (device facts, TWRP, EDL, recovery).

Nothing here ships Hisense or Qualcomm firmware. Scripts expect the stock
firmware package and backups under `$A9_DATA` (see `scripts/a9-env.sh`).
Related: hisense-a9-patcher, hisense-a9-kernel, hisense-a9-twrp.

Licence: Apache-2.0 (LICENSE). Kernel patches under `scripts/mainline/` are
GPL-2.0, like the kernel they modify. See NOTICE.

Flashing a phone can brick it. Read docs/ and the a9-recover runbook first.
