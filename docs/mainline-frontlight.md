# Hisense A9 mainline frontlight

2026-10-15 (UTC). **Offline evidence and implementation; no phone operation was
performed.** `MEASURED` below means directly inspected binary/DT/source or
executed host/build checks. It does not mean that this driver has illuminated
the panel. `INFERRED` identifies wiring interpretation or untested behavior.

The implementation is in [scripts/mainline/frontlight](../scripts/mainline/frontlight/README.md).
It supports the compat shim's four LED target names, with raw 11-bit brightness
and stock electrical profiles. **2026-10-16 correction:** this handset has only
the two AW targets available in stock and run 9. The optional KTD nodes are now
disabled; GPIO88/101 stay reserved. Driver code and stock DT nodes alone did
not establish that four frontlight ICs were populated. Both IC families remain
handled by one small I2C module with separate profiles and a host-tested core.

## Wiring and software ABI

**MEASURED:** golden `lun4/dtbo_a.bin`, `fragment@20` targets
`qupv3_se0_i2c`; `fragment@31` targets `qupv3_se1_i2c` (`__fixups__`). The golden
base DT maps those controllers to 4a80000 and 4a84000. GPIO flags are zero:
active high. These are controller identities, not promised Linux adapter IDs.

| LED class device | Stock driver instance | Mainline controller | I2C address | HWEN | Compat input and gain |
|---|---|---|---|---|---|
| `a9:warm:center` | AW99703 #1 | `&i2c1`, 4a84000 | 0x36 | TLMM83 | `sys.linevibrator_open`, ×0.81 |
| `a9:white:center` | AW99703 #2 | `&i2c0`, 4a80000 | 0x36 | TLMM85 | `sys.linevibrator_short`, ×0.75 |
| `a9:warm:edge` | KTD3137 #3 | `&i2c1`, 4a84000 | 0x35 | TLMM88 | `sys.linevibrator_open`, ×0.78 |
| `a9:white:edge` | KTD3137 #4 | `&i2c0`, 4a80000 | 0x35 | TLMM101 | `sys.linevibrator_short`, ×0.72 |

The KTD rows describe **optional stock software targets, disabled by default**,
not detected chips or optically confirmed edge lighting on this handset.

**MEASURED:** all four LED class `max_brightness` fields in the stock probe
code are 0x7ff. Both register formats are 11-bit. The new driver's
`/sys/class/leds/<name>/brightness` takes raw 0..2047 and
`max_brightness` reads 2047. A name collision fails registration rather than
silently adding a suffix. Only the board-specific compatibles
`hisense,a9-aw99703-frontlight` and `hisense,a9-ktd3137-frontlight` match, so
these electrical defaults cannot accidentally apply to another board using a
generic chip compatible.

**MEASURED software / INFERRED physical meaning:** the patcher's
`patch_system_img.py:703,717,746,752,2544-2550` routes `short` to #2/#4 and
`open` to #1/#3. At 4082 K the Night Light calculation selects `short` only;
the complementary `open` component grows toward 2596 K. Stock charging strings
call AW#2 "center" and KTD#4 "edge" (see [mainline-plan §2.4](mainline-plan.md#24-frontlight)).
This supports white/warm and center/edge respectively. No optical measurement
proves which light-guide region or populated sink each IC drives.

**MEASURED:** the compat daemon already has these four names and performs the
gain/clamp operations in `scripts/mainline/eink-compat/eink-compat.c:223`.
The driver contains **no gain, warmth mix, minimum-brightness lift, or 2200-level
logical scale**. At logical 1000 the shim supplies 810/750/780/720. At logical
2200 it supplies 1782/1650/1596/1473; KTD input clamps to 2047 before scaling.
Thus no ROM patch or daemon argument change is required for this LED ABI.

## Run 9 KTD NACK diagnosis (2026-10-16)

**MEASURED from saved files:** run 9's `android/dmesg.txt` and
`android/eink/hold/fault-000031-dmesg.txt` under
`/home/user/A9-wt/run9/runs/20261015T235605Z-mainline-android/` report `-ENXIO`
for both `0-0035` and `1-0035`. The core returns `-EBUSY` for an incompatible
readable live profile, `-ENODEV` for a readable wrong ID, and success without
I2C reads if sampled HWEN is low. Thus these errors came from I2C reads with
sampled HWEN high. The old log does not identify which register failed or
prove the physical pin state. Read errors now include the register, and the
adoption error includes sampled HWEN. Probe still performs no writes on error.

**MEASURED primary stock evidence:** read-only `debugfs` extraction of
`lun0/kdebuginfo.bin` from `edl_2026-10-11_132444`, directory
`/kmon_log/kexmlog_0`, confirms this predates mainline. Both the earliest
`kmonlog_1970_0101_175433` and latest `kmonlog_2026_0811_105015` contain ABL
R00 failures for both `ktd3133 bl-3/bl-4` followed by “ktd ic not available”.
The latest stock kernel log at lines 1178–1191 also reports R02 failures and
`exit backlight-3/4 probe -107` for buses 1/0 respectively. Stock disassembly
shows HWEN explicitly driven high before those R02 reads. Both AW chips
identify successfully. Failed ABL reads also print changing `chiid` values;
those values are not valid chip identities. See [stock boot logs](stock-boot-logs.md)
and the independent [missing stock backlight interfaces](a16-gsi-port-plan.md#known-broken--known-limited).

**INFERRED:** the KTD pair is an unpopulated optional configuration on this
handset. Physical absence is not proven by teardown, but stock and mainline
both establish unavailable targets. Neither a newly incompatible profile nor
a new R00-only ID check explains stock R02 failure. The safe board fix is to
leave both optional DT nodes disabled and GPIO88/101 reserved. Do not reset
unknown hardware, weaken the live-profile/ID checks, fabricate working edge
LEDs, or treat a NACK as proof of darkness. A different hardware revision needs
positive population evidence before enabling these nodes and their pins.

The `module_load` AVC is permissive and not causal: the module loads and both
AW probes succeed. Late stock `chmod/chown` failures target absent old sysfs
names and occur after adoption. No Android shim/profile writer is shown to
have caused the initial failures. Qualcomm GPIO reads sample an input bit;
the logs lack physical HWEN/mux captures, so no exact electrical cause beyond
the nonresponding targets is claimed.

## Primary stock evidence

The read-only golden backup is
`$A9_DATA/Backups/edl_2026-10-14_110759/`. Verified again during this task:

| Artifact | Size / location | SHA-256 |
|---|---|---|
| `lun4/boot_a.bin` | 100663296 bytes | `679e95b8cba6e2e96ef4f565cf25fb6f7feb74e5c66e2491d1ceb7445ddf88e7` |
| compressed stock kernel | boot offset 4096, length 15047394 | `b1ac57e388353f045d796c280386819072e3ca80819aa9ed8eb4caed7b3fedc5` |
| decompressed Image | 34476544 bytes | `2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1` |
| DTBO entry 0 | dtbo offset 64, length 88658 | `74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931` |

The stock vmlinux regenerated by the [kernel re-assessment appendix](kernel-re-assessment.md#appendix-reproduction-offline-session-scratchpad-kre)
was reused at `/tmp/sol-fp9929a-pg/vmlinux.elf`. Reproduction after extracting
the kernel/DT into scratch files (all commands operate on host files):

```sh
nix-shell -p 'python3.withPackages(p:[p.capstone p.pyelftools])' --run \
  'python3 -I scripts/mainline/kern-dis.py /tmp/sol-fp9929a-pg/vmlinux.elf aw99703_probe aw99703_backlight_init aw99703_set_brightness ktd3137_probe ktd3137_update_brightness > /tmp/sol-frontlight-stock-dis.txt'
nix-shell -p dtc --run \
  'dtc -q -I dtb -O dts /tmp/sol-fp9929a-pg/stock-dtbo.dtb -o /tmp/sol-frontlight-stock-dtbo.dts'
python3 scripts/mainline/dtbo-resolve.py /tmp/sol-frontlight-stock-dtbo.dts fragment@20 fragment@31
```

`kern-dis.py` emits both copies of duplicate symbol names. Addresses below
refer to this exact kernel, not an upstream source reconstruction.

| Stock function | Instance 1 or 3 | Instance 2 or 4 | Relevant behavior |
|---|---|---|---|
| `aw99703_probe` | `ffffff8008f20460` | `ffffff8008f21848` | HWEN high; 3.5–4 ms delay; ID R00=03; max 07ff |
| `aw99703_backlight_init` | `ffffff8008f1fcb0` | `ffffff8008f21098` | DT-derived fixed settings below |
| `aw99703_set_brightness` | `ffffff8008f1fae8` | `ffffff8008f20ed8` | 0.81/0.75 gain; R06 low 3, R07 high 8; R02 mode |
| `ktd3137_probe` | `ffffff8008f226a8` | `ffffff8008f23e90` | successful R02 read only as stock ID check; max 07ff; settings below |
| `ktd3137_update_brightness` | `ffffff8008f234c8` | `ffffff8008f24ca8` | clamp, 0.78/0.72 gain; R04 low 3 then R05 high 8; R02 bit0 |

**MEASURED AW settings:** A9 DT specifies PWM mode 0, using-lsb, full-scale
code 0, channel count 1, map 3, on/off ramp 4/4 and transitions 0/0. Init
executes `R02 |= 0x14`, `R04=(R04&0xe3)|0x10; R04|=0x23`, `R05|=0x40`,
`R03=(R03&7)|(0<<3)` then sets its low bits to 1; `R09=0x44`, `R0a&=0x80`.
Starting from chip reset values, that yields R02=14 (standby), R03=01,
R04=33, R05=40, R09=44, R0a=00. The new driver writes those fixed values
explicitly, including the safety fields; it does not inherit an arbitrary
boost/current setting. Ratio is `(R07<<3)|(R06&7)` and backlight mode R02=15.

The Awinic register descriptions identify those current/boost settings as
4.8 mA per enabled sink, 41.5 V nominal OVP, 3.4 A boost OCP, 1 MHz and
10 µH selection. The boost OCP is distinct from LED current. This reproduces
stock's high OVP/OCP codes while avoiding the chip's much larger reset LED
current. [Awinic AW99703 datasheet, rev 1.6, pp. 27–30](https://doc.awinic.com/doc/202403/4635fa96-003b-4dd8-ad18-b47173a9429f.pdf).

**MEASURED KTD settings:** DT has using-lsb, using-linear, no pwm-mode,
full-scale raw 1, ramps/transitions 0, OVP 0x20, inductor-current 1800,
switching-frequency 1000. Stock writes R03=62, R06|=80, R07=00, R08=00,
R02=01. The new driver starts in standby R02=00 and only writes R02=01 after
the requested ratio has been written. R04 low bits precede R05 for every
update, even when only the low bits change. The datasheet specifies that R05
latches both ratio parts. R02=01 selects 5 mA full scale, flash disabled;
R03=62 selects 32 V OVP, 1.8 A boost limit, 1 MHz, linear mapping; R06 bit7
disables external PWM. [Kinetic KTD3137 datasheet, rev 04a, pp. 18–20](https://www.kinet-ic.com/uploads/KTD3137-04a.pdf).

**MEASURED channel caveat:** stock parses `ktd,bl-channel=2` and stores it at
platform-data +0x1c (`ffffff8008f229b8` / `ffffff8008f241a0`), but neither probe
reads it back to select channels. Its R06 read/OR/write at
`ffffff8008f22cd0..8f22d54` changes only PWM-disable bit7. Therefore the
property is not evidence for writing a mask of 2 or 3. **INFERRED cold-start
equivalence:** reset R06=1f becomes 9f, with CH1..3 enabled. Our cold init
writes 9f; boot adoption preserves any existing nonzero sink mask. The exact
populated sinks remain unmeasured. AW's channel1 DT value, in contrast,
explicitly becomes mask 1 in stock init.

Upstream relatives were inspected as architecture references only:
`drivers/video/backlight/aw99706.c` uses a different ID location, register map
and 12-bit range; `ktd253-backlight.c` uses pulse counting; `leds-ktd2692.c`
uses ExpressWire/flash control. None is register compatible. The
[author's KTD3136 RFC, 2026-09-11](https://lkml.iu.edu/hypermail/linux/kernel/2609.1/09963.html)
uses a similar 11-bit split, but its current/PWM defaults and boot-time
brightness are not the A9 stock profile and were not copied.

## Probe, safety and lifecycle contract

**MEASURED implementation, hardware effects INFERRED pending testing:**

* Request mandatory active-high HWEN with `GPIOD_ASIS`, sample its level and
  validate an already enabled chip using reads only. Then set output direction
  to that same sampled level. There is no reset pulse or HWEN output-level
  pinctrl preset. If HWEN is low, do not raise it or probe I2C: publish level 0
  and defer chip identification to first nonzero brightness.
* HWEN high with standby mode remains dark. An active chip is adopted only
  with backlight mode, PWM disabled, linear mapping, stock full-scale current
  and stock boost profile. AW requires R03=01, R04[5:0]=33 and R05 bit6 set;
  KTD requires R02=01, R03=62 and a nonzero sink mask. Read its raw ratio into
  the LED class without any I2C writes. An incompatible live configuration,
  flash/PWM mode, wrong ID or read error fails binding and leaves the hardware
  untouched. This is a deliberate limit on adoption, not a claim that every
  possible bootloader configuration is safe to manipulate.
* First nonzero after a cold/off state asserts HWEN, waits 3.5–4 ms, checks ID,
  explicitly writes standby, current/boost settings and zero ratio, then
  writes requested ratio low part first and enables backlight last. AW ID
  must equal 03; KTD ID bits [5:3] must equal 011 (stronger than stock's
  successful-read check). Both reset into standby, so reset full-scale ratio
  values cannot illuminate a sink before the safe setup. No software reset,
  MTP programming or flash API is exposed.
* Off first requests standby, then deasserts HWEN even if the bus failed.
  This is stricter than stock's standby-only off and guarantees a cold
  reinitialization on the next request. Each I2C operation gets five attempts,
  2–2.5 ms apart. Any failed init or ratio transaction lowers HWEN and clears
  the initialized state; a subsequent request retries a complete cold setup.
  A failing GPIO controller cannot provide a physical off guarantee; that
  hardware error is returned, never treated as a successful brightness update.
* The mutex serializes per-chip operations. `LED_CORE_SUSPENDRESUME` supplies
  off/restore, while the getter preserves the LED core's resume target during
  suspension. Remove/shutdown unregister triggers and flush LED work before
  the final off. No `retain-state-shutdown` support is enabled.

There is no runtime current-limit tuning. Raw max 2047 at stock full-scale
current is allowed; ordinary ROM use applies its historic per-chip attenuation
in the shim. There is no dependency on a DRM card index or panel power-up.
The stock DT has no frontlight supply phandle, so the driver controls HWEN and
does not invent a regulator or change a shared supply voltage.

**Integration limitation:** LED sysfs writes enqueue a blocking I2C setter on
the core workqueue, so their return value cannot acknowledge the physical
write. The driver logs failures and its getter reports the last confirmed raw
level (zero after a successful fail-safe off). The current shim caches
successful sysfs writes and does not poll this readback. After an I2C failure,
recovery needs another property value or a shim restart; readback-aware retry
is a possible later shim improvement. This task makes no unreviewed change to
the Android vendor or compat source.

## DTS and Android integration

`a9-frontlight.dtsi` merges the existing four `backlight@35/@36` placeholders,
sets label/default-state/board-compatible, and enables i2c1. It reopens only
TLMM **4/5, 83/85** relative to the board reservations. KTD `backlight@35` nodes
remain disabled and TLMM88/101 reserved in both this fragment and the inlined
Android DTS. The golden
base `qupv3_se1_i2c_active` pinctrl at phandle 146 explicitly assigns GPIO4/5
to qup1. GPIO88 also appears as stock sdhc_2 CD; sdhc_2 remains disabled.
Any later reservation changes must be merged rather than overwritten.

Do not copy AW#1's DTBO pinctrl phandle 31 as an HWEN configuration: it
resolves to `fragment@17/epd_top_therm/epd_top_therm_default`, PMI632 gpio4,
high impedance. Its overlay target is `pmi632_gpios`, not TLMM.

The shipped vendor compat rc currently runs as root and its default paths
match these labels. Root-writable LED attributes need no permissive chmod.
Enforcing SELinux needs the two available LED brightness paths labeled
`vendor_sysfs_a9_frontlight`, as anticipated by the existing compat policy;
do not grant the whole controller subtree (it also contains panel power ICs).
The vendor directory was inspected but not edited.

Coordinator-only read-only device-shell check after integration (not executed
here; no i2c-dev access or brightness write):

```sh
for name in a9:warm:center a9:white:center; do
    p="/sys/class/leds/$name"
    echo "$name"
    readlink -f "$p"
    cat "$p/max_brightness" "$p/brightness"
    ls -lZ "$p/brightness"
    readlink -f "$p/device/driver"
done
dmesg | grep -E 'a9-frontlight|Cannot safely adopt boot state|brightness.*failed'
```

This checks names, adoption reports and policy labels, not optical behavior.
Boot with an existing stock light level and compare visually across probe;
then have the coordinator exercise the existing brightness/warmth UI and
suspend/resume. No such hardware validation is claimed by this proposal.

## Offline validation

**2026-10-16 update:** native and ASan/UBSan host regressions additionally cover
`ENXIO`, `EIO` and `ETIMEDOUT` at every adoption read for both chips, requiring
zero writes or GPIO calls. The DT test compiles both the full board plus
fragment and the full Android DTS; both enable only AW, disable both KTDs,
reserve GPIO88/101, reopen exactly 4/5/83/85, and keep sdhc_2 off. It also checks
the Android inline body and hash against the fragment. Current build results
and artifact hashes are recorded in `runs/sol-frontlight-ktd.md`.

**MEASURED 2026-10-15:**

* Native GCC host tests, also ASan+UBSan: both chip kinds, every raw level
  0..2047, no probe writes/enable edges, exact split/latch order, rejection of
  altered current/OVP/PWM/flash states, dark standby adoption, cold restart,
  and read/write failures at every cold-init and active-update operation.
* Full board plus fragment compiles with dtc. The DT test asserts all four
  compatibles, LED labels, addresses and GPIOs, plus exactly six reopened
  pins. Existing board/upstream dtc warnings remain in the test log (QUP
  I2C/SPI alternate unit addresses and display graph/address-cell warnings).
* `dt-doc-validate` passes in a staged kernel binding layout; `dt-mk-schema`
  and `dt-validate -l hisense,a9-frontlight` accept all four compiled nodes.
* Linux 7.2.10 external ARM64 module compiles and links with `W=1`, GCC
  14.3.0, no warnings. Private kernel `O=`:
  `~/A9-build/mainline/sol-frontlight-7.2.10/kernel`; module output in sibling
  `module/a9-frontlight.ko`. Vermagic:
  `7.2.10-a9+ SMP preempt mod_unload modversions aarch64`.
  Read-only reference `.config` and `Module.symvers` came from
  `build-7.2.10-android`; the latter's SHA-256 is
  `ebcac738291e26509a05cc00611c716b3f87c1bbced6df32e405d736940bcc8a`.
  No shared output/source directory was changed. Rebuild with the final
  integrated kernel before use; this compile artifact is not deployed.
* Kernel checkpatch on the driver/core/header: zero errors or warnings.

Commands and review patch integration are in the linked README. Hardware
handoff, sink population, light-guide mapping and enforcing-policy behavior
remain untested on the device.
