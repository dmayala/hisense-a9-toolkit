# A9 hardware shortcuts and factory ADB: firmware verification

Date: 2026-10-16. Offline analysis with the **reverse-engineer-anything** skill,
REA 6.5.0, GNU objdump, Ghidra 12.1.4, and supporting extraction/emulation.
No phone commands, reboots, flashes, or runtime key tests were performed.

The bootloader key mappings and three kernel USB sequences are established by
code. Stock userspace connects two USB sequences to ordinary authenticated ADB.
A **separate factory root-authentication mechanism** also exists; the traced
button path does not enable its root flag.

## Boot shortcuts

| Mode | Firmware-supported input | Evidence boundary |
|---|---|---|
| Recovery | Power + Volume Down during a genuine power-key cold-start | XBL encodes the pair as scan code 8; ABL selects recovery for KEYPAD + 8 |
| Direct bootloader fastboot | Hold Volume Up while inserting USB into a powered-off phone, so USB initiates the cold-start | XBL encodes Volume Up alone as scan code 1; ABL selects fastboot for CABLE + 1 |
| EDL | Early XBL has USB-line detection leading to forced EDL | The exact cable wiring and button procedure were not established by this analysis |

XBL `ButtonsDxe` polls Power, Volume Up (GPIO 5), and Volume Down (PON IRQ 1)
at `0x3b94`, assigns internal IDs 1/2/3, and encodes ID 2 alone as scan 1
(`0x36e0–0x36fc`) and IDs 1+3 as scan 8 (`0x3700–0x3720`).
ABL's key reader returns that scan code unchanged. Its boot-flow handler
compares CABLE (`0x40`) plus scan 1 at `0x36828–0x3689c`, and KEYPAD (`0x80`)
plus scan 8 at `0x36830–0x36858`; output writes at `0x36a58` and `0x36a6c`
select fastboot and recovery respectively. This describes the cold-start
branches, not every possible reboot reason or a recovery loop.

The earlier instruction to release Power at the logo was **not established**
by these branches. Exact hold duration remains untested. Holding buttons on
an already running phone does not by itself satisfy the cold-start conditions.

Early XBL `0xc249e08` logs entry to forced EDL after `0xc248df8` succeeds;
the detector accesses USB PHY registers `0x016130c4` and `0x016130f4`. No
volume-key check was identified in that particular detector. This supports
USB-line detection but does not prove an ordinary cable + both-volume shortcut.
The special EDL cable procedure remains prior operational documentation in
[backup-and-recovery.md](backup-and-recovery.md), not a newly verified key recipe.

## Kernel sequences while USB is active

Up and Down mean separate Volume Up/Down presses. Release each button rather
than holding it: nonzero key events count, so repeat events can affect history.

| Sequence | Kernel output | Stock userspace result |
|---|---|---|
| Up Down Up Down Up Down | `USB_DOOR_ADB_ONLY=BACKDOORADBONLY`, mode 1 | `charging,adb`: ADB only, USB `109b:911f` |
| Up Up Up Down Down Down | `USB_DOOR=BACKDOOR`, mode 0 | `test,diag,adb`: Qualcomm DIAG + ADB, USB `109b:9111` |
| Up Down Up Down Up Up Down | `USB_DOOR_LOGD=LOGDBACKDOOR`, mode 2 | Handler/effect unresolved |

`check_key_report_uevent` at `0xffffff80085e0080` accepts codes `0x72/0x73`
(Volume Down/Up), shifts a byte of history, and compares the low six bits
against `0x38` and `0x2a`, then the low seven against `0x56`. The logging
branch requires fewer than 700 jiffies since the first counted event after
reset. With this kernel's `CONFIG_HZ=100`, that is **strictly under 7 seconds**.
No equivalent timeout occurs on the ADB/DIAG match branches.

`set_usb_backdoor_value` at `0xffffff80085e0070` sets the history byte.
`dwc3_otg_start_peripheral` calls it with `0x80` when starting USB peripheral
mode and 0 when stopping. The worker at `0xffffff80085e0248` emits the selected
uevent. Offline Unicorn emulation of the exact instruction bytes corroborated
both six-press sequences, logging at 699 versus 700 ticks, and no event when
the USB history byte is zero. Workqueue/logging calls were stubbed; this is
not a hardware or Android runtime test.

Stock `vold` at ELF VA `0x2f75c` handles the first two parsed events by setting
`vold.usb.backdoor=none`, waiting a second, then setting `charging,adb` or
`test,diag,adb`. System init copies that property to `sys.usb.config`.
Vendor init starts ordinary `adbd` and links `ffs.adb`, plus the DIAG function
for `test,diag,adb`. **That diagnostic branch does not enter EDL.**

The logging event exists in the kernel. No matching handler was identified
in the examined libsysutils parser/framework observer or direct system/vendor
literal searches. Compressed APK contents were not exhaustively searched.

## Authentication and root

Stock `/system/etc/prop.default` sets `ro.secure=1`, `ro.adb.secure=1`, and
`ro.debuggable=0`. The traced sequence → vold → init path changes USB
configuration, not these security settings. Therefore **with shipped defaults,
the combo should expose authenticated shell-level ADB**. This is a conclusion
from static code/configuration, not an observed connection.

The stock daemon also contains a distinct `debug.rootbackdoor` path. When that
flag is enabled, its signature-packet branch requires `arg1=1`, at least
512 payload bytes, and an additional 256-byte signature verified with RSA/SHA1
against `/system/etc/pubkey.pem`, alongside the ordinary ADB signature check.
The verifier is at adbd ELF VA `0xb2be8`; the packet gate includes `0x90534`
and `0x905a0`. This is a **custom signed challenge response**, not a requirement
for an executable file to carry a manufacturer code signature.

A factory-key authentication branch, conditional on `debug.rootbackdoor` and
the current `root.reload_policy`, can set `root.reload_policy=1` and exit for
a daemon restart. That property bypasses the normal privilege-drop decision.
Stock init also defines `adbrootd` and switches to it when `adbd` is running
and `debug.rootbackdoor=1`. The ordinary root service still checks whether the
build allows it.

**Unknown:** how every possible stock component enables `debug.rootbackdoor`.
No setter was found in extracted system/vendor init, the traced vold path, or
the bounded FTMode scan. FTMode's identified `root.reload_policy` use is a read.
This does not exclude another activation path. Matching private signing keys
and successful factory-root sessions were not demonstrated.

Current custom-OS behavior is also untested. An earlier assertion that the GSI
does not listen was an inference; this investigation does not establish the
current device's userspace or properties.

## Evidence and retained artifacts

Full local artifacts and SHA-256 manifest:
[`runs/20261016-rev-hardware-keys/`](../runs/20261016-rev-hardware-keys/manifest.json).
REA Evidence records contain tool output; interpretations above are analysis
of that output, not claims that REA observed the phone executing it.

| Subject | SHA-256 | REA Evidence |
|---|---|---|
| ABL LinuxLoader | `1dd55dcbb57612f949a93e94c926b3a69924f5941037e0a350ce022e25bc4dab` | `ev_a3cb31f62cab98f217214afc2d2bc53672b80d697328a5460050683adc821aef` |
| XBL ButtonsDxe | `d4c01d46393d18db5fcaefe8c6627e291255b9921b6e0bee172da313c6b6d79c` | `ev_17e4979d3a6f63eb241b184b73259485f2e7edce059f81d530100e07aa324557` |
| Derived kernel function ELF | `448742789d75fe1e91d9443782654c431ddc5e9a222dc267a8dfd018fce9e415` | `ev_81772e636d3581f3583409631125d0603785d8f2f8f61ab9c2c529a0a0804808` |
| Stock adbd | `163e1bc27a4347fa2a45a01c8a77dd8c60708b1f53e413b99990fe370406a522` | `ev_54cf108dda97c8fd45c47d3be5b877b0fe88a31e71bf0a87c29f5d216d0478b3` |
| Stock vold | `89017425f07573c955eb49a79e397bab80184086b3ab1da10e36f0262ad242de` | `ev_d054fded4a3161bcb26f4af7a3a67ff18a52c19c12effe0e91868f8503f7fde0` |

Kernel compressed source SHA-256 is
`b1ac57e388353f045d796c280386819072e3ca80819aa9ed8eb4caed7b3fedc5`;
decompressed source is `2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
The ELF preserves raw image bytes `[0x560070,0x560308)` at their linked
addresses. Ghidra pseudocode Evidence
`ev_7311a5aa9a7c71b6612f97a51ff332e1374ab12cc141eeaced6013aef0f77f79`
uses an additional `0x100000` rebase for the imported slice; the canonical
addresses above are from GNU disassembly/original recovered symbols.

Stock system source is `HLTE556N/super_2.img`, SHA-256
`645323843390057afc5e4cf25a5c78f4115bc4e5a8716e7f85fa8744ea3165e6`;
vendor source is `HLTE556N/super_5.img`, SHA-256
`ff97725746919ab95aaadc1c59777486dcc3fb7f2cbb39ff22e845a235f52204`.
Both were read directly as ext4 with debugfs; selective files were extracted
to scratch. The decompressed boot-stage artifacts come from the golden dump.

Recovery/direct-fastboot mappings, USB sequences, and stock DIAG behavior are
answered statically. Normal versus factory-root authentication is answered
within the traced path. EDL's precise button recipe, the root-flag setter,
logging userspace behavior, and all current-phone runtime outcomes remain open.
