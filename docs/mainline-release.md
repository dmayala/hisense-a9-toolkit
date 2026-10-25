# Mainline development and release images

The default remains the development image and its adb-root sweep. Set
`A9_RELEASE=1` or `--release` on `scripts/mainline/build-integrated.sh` or the
current `reboot/build14.py` family to finalize a release after feature assembly.
Supply `--release-system` with the audited LOS v8 system image and
`--release-keys` with the private signing directory. This produces boot.img,
vendor.img, system.img, SHA256SUMS and release-build.json in a fresh output.
Development builds do not inspect release keys or change their props/services.

This completes agent's parked release finalizer. His worktree and checkpoint
were read only. Host evidence and limits are in
[the release report](../runs/release-config-20261025.md).

## Current image inventory and release changes

Measured from integ26 boot/vendor and its exact LOS v8 GSI, not a phone:

| Setting | Development input | Release |
| --- | --- | --- |
| Global build type/tags | userdebug / release-keys | user / release-keys |
| System partition type/tags | user / release-keys | user / release-keys |
| Vendor type/tags | userdebug / test-keys | user / release-keys |
| ro.debuggable | 1 | 0 |
| ro.secure | 0 | 1 |
| ro.adb.secure | 0 | 1 |
| ro.force.debuggable | 0 | 0 |
| USB default | adb, development ACM/NCM fallback | none; ordinary authenticated debugging can be enabled through Android |
| Host ADB authorization | public key baked into vendor and copied at boot | baked key and copy action removed; normal authorization UI |
| Test access | adb root, loopback root console, Android timed test deadman | root console/deadman removed; root-dependent launchers refuse before transport |
| Signing | release-signed system apps, seven public test-signed system VNDK APEXes, eight test-signed vendor APEXes | preserve audited release app/platform signatures; sign all fifteen APEXes with private container and payload keys |
| SELinux | enforcing boot, zero-permissive precompiled policy; three inherited system_ext CIL permissive declarations | enforcing boot; release compilation also strips inherited declarations and verifies zero permissive domains |

Every loaded build.prop/prop.default is rewritten, including partition build
types/tags and fingerprint suffixes. System defaults are corrected as well as
vendor defaults, so first-write-wins properties cannot retain insecure system
values. The release marker is `ro.vendor.a9ml.release=1`. Acceptance row27
measures these user/security settings only on a marked release; dev remains
informational NOT_RUN. The host receipt verifies actual signing separately;
release-keys text alone is not signing evidence.

The audited system SHA256 is
`14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793`.
The trust profile records all702 APK/APEX bytes and signer certificates. Its
crDroid platform signer `a82af580…` and other legitimate upstream release APK
signers stay intact for shared-UID/signature/data compatibility. The public CTS
test-shim APEX and an optional Exchange APK with a broken signature are removed
in release only. Seven VNDK v28–v34 APEXes and every vendor APEX are signed at
both the outer APK-container and payload AVB layers. Both signatures, embedded
payload key and unchanged filesystem bytes are checked. New system inputs need
an updated signing audit/profile; vendor APKs or compressed APEXes require an
explicit signer policy instead of silently relabeling them release-keys.

## Keys and host invocation

Keys remain outside Git. The existing private store is
`~/.local/share/a9-mainline/keys/mainline-release-20261022`; the NAS key path is
only a symlink to it. The build reads existing keys and never creates or rotates
them. It requires a private0700 directory, releasekey.pk8/x509.pem and one
private0600 `apex/<package>.pem` per re-signed APEX. The seven VNDK payload keys
were not provisioned during this host task. Existing release keys were untouched.

The explicit `release/prepare-keys.py --keys DIR --vendor IMAGE --avbtool TOOL`
command creates missing payload keys only, with exclusive creation; it requires
an already provisioned release container key/certificate. It skips existing keys
and never rotates them. Back up the actual private store before using a release.

For an existing frozen integrated pair, the standalone host finalizer avoids
rebuilding feature binaries:

```sh
python3 -I scripts/mainline/release/build.py \
  --boot /path/to/integrated/boot.img \
  --vendor /path/to/integrated/vendor.img \
  --system /path/to/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img \
  --keys /path/to/private-release-keys \
  --out /path/to/new-release
```

Alternatively add `A9_RELEASE=1`, `--release-system` and `--release-keys` to the
usual host integration build command. The common integ builder finalizes the
actual cumulative images and includes system.img/signing receipts in its hashes.
Release run.sh/run-fingerprint.sh refuse the root-dependent development sweep;
dev launchers are unchanged. No finalizer flashes, mounts images, provisions
phone authorization, accesses secure storage, or rewrites an input image.

## Services and diagnostics without adb root

| Facility | Release behavior |
| --- | --- |
| Early kernel/logcat/crash logger | PID1 starts the ramdisk logger as root before Android init. It keeps its enforcing diagnostic-domain transition and existing kdebuginfo storage; no adb root dependency. |
| kdebuginfo | Logger keeps mounting/writing the existing filesystem. Private0600 logs stay private; direct shell pulls no longer work. No world-readable export or general root RPC is introduced. |
| Fallback logd, tombstoned/debuggerd, ANR/dropbox writers | Existing init services and ownership remain; capture stays available even when the framework crashes. Root-based sweep extraction is development-only. |
| Wrapped metadata/data, read-only persist, QSEE/RPMB bridge, audio/camera/video, Wi-Fi/BT, sensors, E Ink and USB | Existing init-managed services/domains remain. Retain the `a9ml_rootsh` SELinux **domain**, which functional mounts/logger use, while removing the interactive root-shell **service**. |
| E Ink gate, temporary routes and diagnostic probes | Preserve existing init triggers and gate policy. Host scripts that directly write sysfs or issue root-console commands cannot operate on release. This configuration does not grant new power behavior or prove daily startup readiness. |
| Firmware install/module reload/raw debugfs/manual service controls | Root-dependent developer tools stop working; use images containing their packaged init-managed startup. No fallback root shell is retained. |
| Development sweep/deadman and private log pull | Not a release test path. Their root/USB assumptions are deliberately not emulated; the current release launchers stop before any transport. |

PHH remote/debug property actions, test-awake settings and USB ACM/NCM console
links are removed. Functional PHH/init maintenance helpers are retained. The
existing kernel boot-completion trigger, watchdog owner and boot bytes remain;
the release finalizer does not remove the watchdog or invent a new shutdown path.

Host tests cover real APEX signing with temporary keys, actual policy linkage,
metadata-preserving offline ext4 replacement/readback and a complete small-image
finalization. Full-size release assembly was not run: free space was about17GiB,
and the build requires the15GiB reserve plus system/vendor copies and1GiB signing
workspace. The finalizer refuses insufficient space. No other worker's files
were removed to make room. Fresh-data/setup, APEX-update migration, diagnostic
availability and ordinary app behavior still need coordinator-owned phone
qualification. This is not bootloader relocking, boot-image AVB signing, OTA
delivery or CTS certification.
