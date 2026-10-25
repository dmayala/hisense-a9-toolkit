# Integ 20261023-6r: first-boot and warm-reboot repair

Host-only [credential/RPMB sweep summary command](CREDENTIAL-SUMMARY.md).

Follow-up: [6r2 shared firmware-path repair and sweep diagnosis](README2.md).
Latest: [6r3 integrated audio restoration and remaining sweep failures](README3.md).

Host-built candidate, **not hardware qualified**. No adb, fastboot, EDL, phone,
credentials or playback used. Existing wrapped-key unwrap and Wi-Fi/telephony
startup recovery remain runtime questions; host checks cannot prove them fixed.

Artifacts: `/home/user/A9-build/mainline/integ-20261023-6r/` contains `boot.img`,
`vendor.img`, `SHA256SUMS`, `integrated-build.json`, module audit, build/test logs,
REA evidence, and final source handoff receipt. Default sweep APKs rebuilt at
`/home/user/A9-build/mainline/acceptance-sweep-20261023/`.

## Findings and changes

Sweep5 is `/home/user/A9-build/mainline/sweep-integ5-5-20261023/`, reboot
`boot-0000000072`. Sweep6 is `/home/user/A9-build/mainline/sweep-integ5-20261023/`,
**first boot** `boot-0000000073`. These are not exclusively warm-reboot defects.

| Failure | Evidence and root cause | Correction / limits |
|---|---|---|
| CAMSS CSID2 `-EBUSY` | Sweep5 kernel1215; sweep6 kernel1191. Actual shipped ELF register/IRQ rows are **csid0,csid2,csid2**. Second row claims0x05c7c000; third requests it again. DT correctly declares distinct csid0@5c6e000, csid1@5c75000, csid2@5c7c000. Cached camera source `cameras-20261020/kernel/drivers/media/platform/qcom/camss/camss.c:911` contains the same wrong CSID1 assignment. | Rebuild from pristine Linux7.2.10 plus committed `cameras/camss-sm6115.patch`, already correct. Audit actual ELF rows0,1,2. No DT/reserved-memory fix warranted. Full builds now reject cached wrong resource tables. |
| Sensors persist refusal | Sweep5 kernel1245; sweep6 kernel1242, `fail:persist-already-mounted`. Actual wrapped metadata script deliberately binds `/metadata/a9-tee-persist` onto `/mnt/vendor/persist` before Keymaster; sensor script then refuses that mount. | Move production RAM staging to `/mnt/vendor/a9-sensors`, rebuild listener with root `/mnt/vendor/a9-sensors/s1-listener-root`. Preserve DSP path allowlist `/mnt/vendor/persist/sensors` mapped inside private root. Keep tmpfs0700, no-symlink/same-device/seccomp checks, pins, single ADSP owner, recovery disabled/no restart. Never scan or unmount TEE/real persist. Integrated builder stages matching script/listener. |
| Audio loader1, mic zero samples | Sweep5 kernel1989; sweep6 kernel1978, exit1 after66.384s. Sensor failure precedes ADSP/card startup. Sweep6 onb43: permission_mic1, mic_muted0, record_initialized1, record_started1, samples0/peak0. Successful app initialization does not prove a PCM source. | Defer card loader until existing sensor owner ready1. post-fs-data leaves audio.ready0/state waiting-adsp. Existing HAL restart on card readiness retained. No independent ADSP start or output route. Microphone amplitude still requires remeasurement. |
| Wrapped-data mount | INTEG12 boot100 saved evidence: Begin`-8`/INCOMPATIBLE_BLOCK_MODE, diagnostic characteristics`-33`, vold25. LOCAL clear tags: hardware AES/GCM/decrypt1; OS160000/system202605/vendor+boot20220405, explicitly unverified. Initial hook misses records because Toybox BRE lacks escaped alternation; post-pull capture has KEY/LOCAL. | Vendor patch and boot-header corrections remain in place; decryption unresolved. Fix capture with a Toybox-compatible expression and retain the exact numeric whitelist. Need stock reopen of the SAME current metadata key/secdiscardable, with Begin/characteristics statuses, before changing key/TEE behavior. No cipher/provider/secure-boot changes or key deletion. See [FBE13.md](FBE13.md). |
| Codec timeouts | Sweep6 kernel1257–1260 rejects signed firmware6.0.39 against minimum6.0.55, probe`-22`. Packaged module lacks existing SM6115 compatibility patch. | Fresh Venus modules from committed `video/sm6115-stock-firmware.patch`; actual ELF SM6115 minimum39, QCM2290 minimum55. Preserve decoder-only SM6115 resources and old-firmware EOS quirk. Full build checks actual ELF versions. Hardware encoder remains unsupported; only completed inventory with no hardware yields NOT_RUN; timeout remains FAIL. |
| Wi-Fi255/cap1 | Sweep6 logcat39103/40435: `WifiService: Received shell command when boot is not complete!`; controller request0, framework enable rejected before cap. No RF regression established. | Bounded read-only status readiness before enable/cap/credentials. Permanent startup failure still FAILs without requesting credentials. Existing FIX6 radio preparation retained. Internal WifiService recovery **unproven**; no fake-ready property or validation. |
| ANRs31 | Repeated `com.android.phone ... failed to complete startup`, sweep6 logcat7041/9831. Similar startup failures already occur in integ4 boot55 logcat465 onward. Saved logs lack the ANR stacks needed to identify its blocking call. | Keep failure. Initial current-boot crash/ANR events now count against stability instead of becoming a clean baseline. No telephony disablement or claim that FIX6 radio startup proves recovery. |
| MTP configured0 | Sweep6 logcat13045/13115 applies functions5,13379 reverts to1; MtpService13056 advertises `/dev/null` storage. Direct shell-to-vendor USB HAL dump also hits enforcing binder denial. | After verified initial mute dismiss only unsecured keyguard. Query framework `dumpsys usb` applied/current MTP/connected/configured state; independently require same-device host ADB+MTP descriptors and successful session. No policy broadening. Failed real storage may still fail MTP. |
| FBE under RAM data | Sweep6 onb7/50: one `/data` mount, tmpfs1/f2fs0. | Row29 NOT_RUN/not_applicable1 on positively confirmed tmpfs.6r declares `ro.vendor.a9.data.mode=real-wrapped`; row01 still FAILs for RAM fallback or missing data.ready. Intentional RAM applicability cannot qualify real FBE. |

Row01 restart remains FAIL after changed boot ID/completed B boot until pinned
APK reinstall, verified mute, app camera_count>0/sensor_count>0, sensor.ready1,
audio.ready1 and selected data health. Missing services/probes/timeout cannot
pass. Health only enumerates; it opens no camera, records/plays no audio. Mute
still requires AudioManager flags0 and exact stream minima before tests or
keyguard dismissal.

Event snapshots use the SHA256 route already successful in the BT observer,
with numeric failure stages. Original events_rc126 does not identify a utility;
this repairs/probes collection without claiming cksum caused it. Raw log,
account and network values never enter acceptance.json.

## Earlier runs and stock comparison

Integ2 `b-try-20261023T120128Z-3872376/onb.log:28–36` proves Wi-Fi connected,
Internet validated, check-in, Play signed-out HOME, BT enabled/no crash and
cleanup. It does not prove a signed-in app install. Also compared row28 run5
`b-try-20261023T070722Z-1966335/onb.log` and modem-parity
`b-try-20261023T110628Z-17176/onb.log`. Integ4 EInk
`eink-quality-20261023/mainline4/boot-0000000055/kernel.log:1042,1045` proves
68-byte wrapped key/native AES-XTS; its logcat also has phone startup failures.
Earlier successes are not current sweep acceptance.

Extraction from integ2,4,5 vendor.img finds identical sensor scripts, wrapped
scripts, CAMSS/Venus modules and vendor2025 patch props. All three CAMSS SHA256:
`464c8383aae8335c66ca8422908b484ba6869e2d846ee921a8bf601e9630c4ce`.
The confirmed defects are shared packaging/integration defects, not an evidenced
source regression between these images. Earlier camera/sensor captures may
have selected different per-run overlays. Shared metadata can retain key state
across slots; Linux mount tables and devm claims do not persist into the next
kernel. The sensor bind is created anew every boot.

Offline stock vendor `android/ref/vendor_a.img`, `etc/init/hw/init.target.rc`:
lines133–134 wait for Keymaster then mount_all --late;53–55 mount early/setup
persist;104 creates persist/data. Stock fstab.default46 mounts real persist.
Stock TEE and sensor registry use separate persistent subdirectories; our
private RAM broker must not replace the whole TEE mount.6r preserves the TEE
bind and key persistence. No phone partition read/write used for comparison.

Offline REA evidence under6r (both sessions closed):

- `camss-rea.json`, `ev_01690e3d4e47070d5de8bb5c780c40b5cf981300559dbe41ca66601ee75573bf`:
  msm_csid_subdev_init uses each row's register name for ioremap_resource_byname.
- `keymaster-rea.json`: constructor
  `ev_8beb99ff64ca66416b4940a161c3eec31e2b4460b63bd167390e49aed9805896`, configure
  `ev_80b5c0f1e6f8d5b187fe64f8b6ac08aafb347b9d82a081d2cd4c764078dca0f1`, upgrade
  `ev_385e8636da1dd0767cea01c340896f940ebf1bd3408eb0382b1925c78bf80ef4`.

## Build and validation

Base is ready FIX6 outing (source1d7ed31, main validation262f326), preserving
fingerprint repair, GPIO11 falling-edge UART wake, dead-man and boot radio setup.
Boot/kernel/DT/ramdisk remain identical to its ABL-validated boot.

```text
boot.img   853710225e508e6514ad48137449e0836916ee0ed0060e980bc9f2daad4c9c16
vendor.img 7a31401e4dab3f333fd4ee2d840c3838b77dc7c338c7df1400137d6a77d592cf
```

`python3 -B scripts/mainline/reboot/build.py` uses pinned FIX6 inputs, cached
tools, no network/mounts. Use fresh --out; completed/partial images are not
silently overwritten. Fresh module source is pristine linux-7.2.10 plus exact
committed patches; generated headers/scripts/symbols copied to private objects.
Shared broken caches untouched.100 packaged modules pass native CRC/vermagic
audit (6202 imports/zero bad modules).1305 unchanged regular vendor files compare
byte-for-byte. Changed file mode/uid/gid/SELinux label/timestamps preserved.
Readback/e2fsck/host_init_verifier pass; disk free stayed above15GiB.

Host self-checks, no independent hardware review/qualification:

- Five `reboot/selftest.py` groups: actual packaged media ELF/ABI/readback,
  Android mksh/Toybox RAM staging beside TEE canary/refusal controls, wrapped
  identity/no added storage mutation, audio owner dependency, Android event
  snapshot protocol. `reboot-selftest.log` PASS.
- Updated sensor fixture uses new path with TEE bind/canary in every case;
  existing lifecycle/refusal checks pass. Optional real SNS probe explicitly
  skipped without supplied binary. `sensors-setup-test.log` PASS.
- Five acceptance fake groups: tmpfs applicability/failed real data,
  incomplete Wi-Fi without credentials, reboot missing services/reinstall,
  initial31 ANRs, MTP applied state, mute fail-closed. `acceptance-selftest.log` PASS.
- Three APKs built/signed/verified offline; `acceptance-build.log` PASS.
  Asset/source pins checked against final main; no APK installed.

The owning next run should use these exact boot/vendor artifacts in the
existing real-userdata flow. Intentional RAM data makes row29 inapplicable,
but cannot qualify real FBE. Retain first storage/key-service failure evidence;
do not delete/format keys to obtain an acceptance pass.
