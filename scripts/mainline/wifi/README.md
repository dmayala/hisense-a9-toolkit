# A9 mainline Wi-Fi: default-off, staged coordinator trial

2026-10-16. The user authorized Wi-Fi phone trials. This round builds and tests
host artifacts only; no device is accessed. Source-audited companion services
remain confined to golden-seeded noswap RAM. The coordinator alone may read
four real stores for the explicitly authorized before/after integrity audit.
No modified EFS is ever written back.

Read these together:

- `docs/mainline-wifi-integration.md`: DT/config, private MAC and firmware lookup.
- `docs/mainline-wifi-diag-audit.md`: pinned DIAG source, reduced protocol and limits.
- `docs/mainline-wifi-rmtfs-audit.md` and `mainline-wifi-tqftpserv-audit.md`: storage capabilities.
- `docs/mainline-wifi-sandbox.md`: native mount/FD/UID/seccomp boundary.
- `docs/mainline-wifi-android-round2.md`: matched A16 Cuttlefish stack and limitations.
- The staged coordinator guide: preliminary offline checks, one start, monitoring,
  read-only store comparison, clean stop and existing slot-B rollback.

`A9_WIFI=1`/`--wifi` is required at every radio build stage. The ordinary E Ink
build stays unchanged. `A9_WIFI_ANDROID=1` additionally stages the exact cached
A16 HAL/supplicant/hostapd bundle; it does not enable a radio or Android service
at boot. Android runtime startup has a separate nonpersistent readiness gate.
No persistent property starts companion services automatically.

The five pinned upstream repositories are in
`~/A9-build/mainline/sol-wifi/src`. The lock records full commits and deterministic
archive SHA256s. Build/stage helpers never download or execute an ARM64 binary.
The static build produces rmtfs, tqftpserv, optional pd-mapper, confined
modem-only diag-router, coordinator-only qrtr-lookup and the native launcher.

```sh
python3 -I scripts/mainline/wifi/build-userspace.py
scripts/mainline/android/build-kernel.sh --wifi kernel
scripts/mainline/android/snapshot-kernel.sh --wifi
A9_WIFI_ANDROID=1 scripts/mainline/android/build-vendor.sh --wifi --eink
scripts/mainline/android/pack-boot.sh --wifi -r /private/ramdisk.cpio.gz -c /private/wifi-cmdline
```

The reviewed Wi-Fi command line must fit the ABL's effective 511-byte field and
select exactly `firmware_class.path=/mnt/a9-wifi/firmware`. The root-owned fixed
prep helper bridges immutable vendor radio files there before module loading;
its `hisense` link preserves the existing private E Ink firmware push directory.
Do not replace the lookup path with only the radio or display location.

Private firmware, `private/{modemst1,modemst2,fsg,fsc}.bin`,
`private/wlan_mac.bin`, and their verified inventory stay under `~/A9-build`.
Generated includes/DTBs/boot/vendor images contain private device material and
must never be committed or published. Runtime seeding never reads a real store.

Host tests are under `tests/mainline-wifi/`. Backend/DIAG tests compile actual
patched C with ASAN/UBSAN and fake transports; coordinator tests execute the
real shell against temporary synthetic paths only. `sandbox.py` requires host
root for actual noswap namespace/seccomp probes using synthetic x86_64 binaries.
A host pass does not establish A9 firmware tolerance, RF behavior or target
Android service execution. Follow the preliminary target-only gates first.

Pinned coordinator tools are now cached locally: static ARM64 musl strace6.16
and iw6.17, recorded in `coordinator-tools.lock.json`. Offline host staging is
optional until preparing a coordinator trial:

```sh
python3 -I scripts/mainline/wifi/stage-tools.py --check
python3 -I scripts/mainline/wifi/stage-tools.py --out "$HOME/A9-build/mainline/sol-wifi/coordinator-tools"
```

The output directory must be new, private and outside git/golden inputs. The
helper verifies pinned hashes, ELF static ARM64 metadata and cached strace
exit-kill source/help evidence, then copies the two tools plus current controller,
awk audit, lock and `SHA256SUMS`. It never runs an ARM64 binary, fetches anything
or contacts/deploys to a phone. Runtime rejects strace/iw hash arguments that
differ from the pins. Restage to a fresh directory when coordinator source changes.

Credit: PaddleStroke's read-only Hisense A6L mainline project at
`ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d` supplied the RAM-copy/DIAG-before-modem
pattern and Android integration reference. Its real-partition backend and
write-through option are not imported. SM6115 uses q6v5_pas/PAS4, not SDM660's
q6v5_mss/MBA. DIAG remains a precautionary dependency until A9 measurements.
