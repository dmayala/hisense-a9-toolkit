---
name: a9-edl
description: Drive the Hisense A9 (HLTE556N) over Qualcomm EDL/9008 from Linux using bkerler's edlclient (scripts/edl, scripts/edl-cli.sh) to read and write partitions, dump and repair the GPT, and inspect A/B slot state. Use whenever the phone must be accessed in EDL mode, or when a boot/recovery/slot problem needs partition-level inspection.
---

# Driving the Hisense A9 over EDL

Working tooling lives in `~/A9/scripts/`; paths come from `scripts/a9-env.sh`
(`A9_DATA`, `A9_FW`, `LOADER`, ...). Read this before touching the device —
most of the traps below cost hours to find and are invisible when hit
(commands report success while doing nothing).

## Which tool

**bkerler `edlclient`**, installed system-wide by `~/host-config/a9-usb.nix`
(nixpkgs `edl`, plus a udev rule giving `adbusers` access to `05c6:9008`, so no
root). Call it through **`scripts/edl`**, which adds `--loader=$LOADER` and, for
storage commands, `--memory=ufs`. Forgetting `--memory=ufs` makes edl assume
eMMC with 512-byte sectors.

`scripts/edl-cli.sh` wraps the common operations and covers the common subcommands:

```bash
cd ~/A9/scripts
./edl-cli.sh                         # printgpt, all LUNs (read-only)
./edl-cli.sh gpt 4                   # one LUN
./edl-cli.sh slots                   # dump LUN4 GPT + decode A/B bits (read-only)
./edl-cli.sh read <lun> <start> <count> <outfile>
./edl-cli.sh reset                   # reboot out of EDL
./edl-cli.sh write <lun> <label> <file>      # by partition name
./edl-cli.sh writeraw <lun> <start> <file>   # raw sectors
./verify-partitions-edl.sh           # diff key partitions vs a backup (read-only)
```

`scripts/partition-map.txt` has `label lun start_sector num_sectors` for all 96
partitions. Always take LUN and start sector from there rather than guessing.

Getting into EDL: `adb reboot edl` from Android (verified working
2026-10-10), or the EDL cable: reboot with it unplugged, then plug in holding
Power + Vol Up + Vol Down + the cable's button. Confirm with
`lsusb -d 05c6:9008`. **The hello is sent once, at enumeration**: have edl
already waiting, or run it straight away. A phone left in EDL for a while, or
across a VM restart, is usually wedged.
Getting out without firehose loaded: hold Power ~10–15 s.

## The traps

**1. One Sahara hello per EDL entry.** The device sends its Sahara hello
exactly once, when it enters EDL. If it is missed, the device sits silent and
nothing short of a fresh EDL entry recovers it (verified 2026-10-10 on this
server: neither a USB reset nor a Sahara `RESET_REQ` gets a reply). edl handles
the *normal* case — after the programmer is loaded, later calls detect firehose
mode and skip Sahara — so the old `connect` + `SAHARA_DONE=1` dance is gone.

**2. edlclient hangs forever on a silent device.** When no hello arrives it
falls into `sahara.connect()`'s last branch, whose read loop increments a
1500 ms timeout looking for `== 10` and never exits. The symptom is
`main - Device detected :)` and then nothing. `timeout` your calls; if it
hangs, the hello is gone — see trap 1.

**3. EDL needs the USB controller passed through over PCI, not usb-host
passthrough.** Under Proxmox/QEMU usb-host passthrough the PBL never sent a
Sahara hello and refused all data: 5 attempts, edlclient and qdl, USB-C and
USB-A. That held even with a reader attached 2 ms after enumeration
(usbmon-verified, 2026-10-10). Since the host passes the Alder Lake PCH xHCI
(`00:14.0`, in the guest `06:10.0 [8086:51ed]`) to the VM, plain
`./edl-cli.sh gpt 4` after `adb reboot edl` works first time. If EDL ever
stalls at `Device detected :)` again, first check that `lsusb -t` shows the
phone under that controller and **not** under `QEMU XHCI [1b36:000d]`.

**4. Use the `_001360E1` loader.** `prog_firehose_ddr.elf` and
`prog_firehose_ddr_001360E1.elf` are both 638,972 bytes but **differ from byte
4161**. Measured 2026-10-10: `_001360E1` is signed by the **Hisense Root CA**
(production); `prog_firehose_ddr.elf` by a **"Generated Test Attestation CA"**
(Qualcomm test-signed), which the phone should reject. Using the wrong one is
the remembered "download mode freezes" failure. `a9-env.sh` picks `_001360E1`. There are also `prog_firehose_lite*.elf`
variants as a fallback.

**5. `--debugmode` writes `logs/log.txt` into the cwd** and crashes with
`FileNotFoundError` if `logs/` does not exist. Run it from a scratch dir with
`mkdir logs` first. Its stdout is block-buffered through a pipe; use
`PYTHONUNBUFFERED=1` or you lose the output on a kill.

**6. `edl qfil` may set the bootable LUN.** After programming and patching it
calls `setbootablestoragedrive` if the rawprogram XML names `xbl`/`xbl_a`/`sbl1`.
`restore-factory-gpt-lun4.sh` uses a two-entry XML that names none. Do not feed
it a full factory `rawprogram*.xml` casually.

## Device layout

**UFS, 4096-byte sectors, six LUNs, each with its own GPT** (hence
`gpt_main0..5.bin` in the stock package).

| LUN | Holds |
|---|---|
| 0 | `super`, `userdata`, `metadata`, `misc`, `persist`, `recovery_a/b`, `logo_*`, `keystore`, `frp`, `tpf_*` |
| 1 / 2 | `xbl_a` / `xbl_b` + configs |
| 3 | `cdt`, `ddr` |
| 4 | `boot_a/b`, `vbmeta_a/b`, `dtbo_a/b`, `modem_a/b`, `devinfo`, `dip`, `apdp`, `secdata` |
| 5 | `modemst1`, `modemst2`, `fsc`, `fsg` (modem NV / IMEI) |

## A/B slot state is in the GPT — READ IT, NEVER WRITE IT

Slot state lives in the GPT partition attribute bits of `boot_a`/`boot_b` on
**LUN4** — not in `misc`, not in any partition:

```
bit 48-49 priority   bit 50 active   bit 51-53 tries   bit 54 successful   bit 55 unbootable
```

> ### ⚠ Hand-patching these bits bricked this device on 2026-08-08
>
> A six-bit edit — correct values, both CRC32s recomputed, byte-diff showing
> exactly the 10 intended bytes changed, read-back from the device confirming
> it, and the bootloader's own `getvar` agreeing — still left the device unable
> to load any boot image:
>
>     fastboot continue -> FAILED (remote: 'Failed to load image from partition: Device Error')
>
> on **both** slots, with a pristine factory boot image. Hours were then spent
> wrongly suspecting `misc`, `metadata`, `/data` (wiped — unnecessarily) and
> `super`. Restoring the factory GPT fixed it instantly.
>
> **Every verification available said the patch was correct. It was not.**

### Reading slot state — safe, and genuinely useful

```bash
./edl-cli.sh slots          # = edl rs 0 34 gpt-lun4.bin --lun=4 + parse-slot-attrs.py
```

This correctly diagnosed `boot_b` as unbootable. Diagnosis good, write bad.

### Changing slot state — use the bootloader

```bash
fastboot set_active a
```

Works even on a slot flagged `unbootable`, and resets its retry count. This is
the **only** supported way.

### Repairing a damaged GPT — use the factory table

```bash
./restore-factory-gpt-lun4.sh            # checks inputs only
./restore-factory-gpt-lun4.sh --write    # edl qfil: program + patch4.xml
./edl-cli.sh reset
fastboot set_active <slot>   # factory GPT leaves all slot attrs zero; pick the slot WITH a system
```

It writes `gpt_main4.bin` + `gpt_backup4.bin` **and applies `patch4.xml`** — 26
fixups computing last-usable-LBA and CRC32s against the real disk size. **That
step is not optional**; the factory files are generic and the patches make them
correct for this device. It is the likeliest reason a hand-patched table fails
where the factory one works.

`scripts/patch-slot-gpt.py` is kept only as a record. **Do not use it.**

## `misc` is not just the BCB

On this Virtual A/B device `misc` also carries a `misc_virtual_ab_message` at
**offset 32768** (`version, magic 0x56740AB0, merge_status, source_slot`).

**Never `fastboot erase misc`** — it destroys that structure along with the
BCB. To clear a stuck BCB while preserving VAB state, zero bytes `0..32767`
and keep the rest. Also check the BCB before restoring any `misc` backup: a
backup taken after `adb reboot bootloader` contains `"bootonce-bootloader"` and
will send the device straight to fastboot on every boot.

## Known-bad states and what they mean

| Symptom | Meaning |
|---|---|
| `Device detected :)` then nothing | Hello missed (trap 1/2). Hold Power 10–15 s, re-enter EDL. |
| `Access denied (insufficient permissions)` | udev rule not applied — check `a9-usb.nix` is imported and you are in `adbusers`. |
| `fastboot boot` → `unknown command` | Unsupported on this bootloader. Not a fault. |
| `fastboot continue` → `Failed to load image from partition: Device Error` | Bootloader cannot read a boot image. Seen on **both** slots with verified-good images; unresolved. |
| Device shows as `900E` not `9008` | Not in EDL — that's the diagnostics port. |

## Legacy: Windows (retired 2026-10)

Until 2026-10 EDL ran from a Windows PC (QPST tools, usbipd-win). Removed; see
git history. None of it applies on build-host.
