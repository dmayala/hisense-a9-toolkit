# INTEG26 host delivery

Source main commit: `fba29133df146a073046092166a0640cdfc9549a`.
KMTB guard commit: `9897f8e43ed740fbe2b782912ff7a3e5ad9a4d99`.
Frozen worktree: `/home/user/A9-wt/integ26`.
E Ink dip-quota removal was not on main at freeze.
Later encoder startup variants `57544b1` landed after assembly and are not included.

| Image | Bytes | SHA256 |
|---|---:|---|
| /home/user/A9-build/mainline/integ-20261024-26/boot.img | 14008320 | `3cda45f09df9e8130cc82edca7dd0cf8860f754dfe99c60fb5d9a2b85c65a1c4` |
| /home/user/A9-build/mainline/integ-20261024-26/vendor.img | 847532032 | `1f440bf488abf1e4006ee37cbea79fe7301ef32e05030afd3dbee8db702bdd06` |

Build rc=0; images exist; SHA256SUMS verified. Host only; no flash or phone access.
FORMAT defaults to 0 and honors overrides. Physical persist remains read-only.
RPMB scope: `learned-gatekeeper-and-stock-single-kmtb-entry`.
Fresh Venus modules include 310281c. Standard `run.sh` leaves
`venus_enc.a9_mapped_eos=N`; `run-mapped-eos.sh` explicitly writes and reads
`Y` before the ordinary acceptance hook and attempts `N` restore in finally.
The parameter is sampled only when an encoder instance opens.
Mapped hook success/failure/reset paths and HVENC/PIN forwarding were tested
with inert transports. No hardware qualification is claimed.
Host evidence: `/home/user/A9-build/mainline/integ26-host`.

A: mapped EOS off (coordinator only; separate formatted-B sweep):
```sh
cd /home/user/A9-wt/integ26
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ26-f1-eos-n-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 \
/home/user/A9-build/mainline/integ-20261024-26/run.sh \
> /home/user/A9-build/mainline/sweep-integ26-f1-eos-n-20261024.out 2>&1
```

B: mapped EOS on (coordinator only; separate formatted-B sweep):
```sh
cd /home/user/A9-wt/integ26
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ26-f1-eos-y-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 \
/home/user/A9-build/mainline/integ-20261024-26/run-mapped-eos.sh \
> /home/user/A9-build/mainline/sweep-integ26-f1-eos-y-20261024.out 2>&1
```

