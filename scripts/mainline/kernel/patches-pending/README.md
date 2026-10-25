# Pending kernel patches (NOT applied by build.sh)

Status: 2026-10-15. `build.sh` applies only `../patches/`; nothing here reaches an
image until it is moved there.

Currently empty. The former `0022-regulator-fp9929a-wait-for-PG-active-high...`
was moved to `../patches/` after E3 boot 9 measured PG on the phone
(`runs/20261015T142350Z-mainline-first-boot`, not in git): PWRUP alone 0, steady
on 1 (2 of 2 samples >= 20 ms after VCOM_EN), settled off 0 (54 of 54), and the
kernel log bounds the settle time after VCOM_EN to <= 60 ms (`on:` with PG 0 at
163.830 s, a9_eink's check saw 1 at 163.890 s); the samples show ~20 ms.
`pg_timeout_ms` stays 100 (>= 1.7x the bound, ~5x the sampled value).
