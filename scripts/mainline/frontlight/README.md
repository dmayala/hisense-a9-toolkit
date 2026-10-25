# A9 frontlight driver proposal

2026-10-15. Offline implementation and compile checks complete; **not tested on
the phone**. Electrical evidence and limits: [mainline-frontlight.md](../../../docs/mainline-frontlight.md).

`driver/` builds one I2C module, `a9-frontlight.ko`, containing separate AW99703
and KTD3137 profiles. The supported raw LED names are:

| LED class name | IC | Controller | Address | Active-high HWEN |
|---|---|---|---|---|
| `a9:warm:center` | AW99703 #1 | i2c1, 4a84000 | 0x36 | TLMM83 |
| `a9:white:center` | AW99703 #2 | i2c0, 4a80000 | 0x36 | TLMM85 |
| `a9:warm:edge` | KTD3137 #3 | i2c1, 4a84000 | 0x35 | TLMM88 |
| `a9:white:edge` | KTD3137 #4 | i2c0, 4a80000 | 0x35 | TLMM101 |

**2026-10-16 correction:** stock ABL and stock kernels cannot detect either
KTD on this handset; the stock KTD backlight interfaces are absent. The KTD
rows are optional software targets, not detected edge LEDs. Both nodes now
remain disabled and GPIO88/101 reserved in the fragment and Android DTS. Only
the two AW devices are enabled. Positive hardware population evidence is
required before enabling a KTD variant; a NACK does not establish darkness.

Registered devices expose `brightness` and `max_brightness=2047` under `/sys/class/leds/`.
No gains are applied here. The compat daemon's existing defaults already match
these names; it applies 81/75/78/72 percent once. Warm/white and center/edge are
software-derived interpretations pending optical confirmation. One LED device
represents the bank controlled by an IC, not an individual sink: AW enables CH1;
KTD cold initialization uses the reset CH1..3 mask, matching stock's lack of a
channel-mask write. Its DT `bl-channel=2` does **not** establish a two-sink mask.

Probe keeps HWEN and the ratio registers unchanged. A dark chip stays dark
without even being powered up for ID validation. A lit chip is adopted only
when it has a recognized stock electrical profile; otherwise probe fails and
leaves it untouched. First use configures current, OVP and zero brightness in
standby before enabling backlight. I2C failures lower HWEN. System suspend turns
the LEDs off and the LED core restores the requested level on resume. Remove
and shutdown turn them off. There is no panel/TCON dependency and no flash API.

## Kernel agent integration

1. Review `patches/0001-leds-a9-frontlight-PROPOSAL.patch` and apply it to your
   kernel checkout. It adds the driver under `drivers/leds/a9-frontlight/`, its
   Kconfig/Makefile hooks and the DT binding. Enable `CONFIG_LEDS_A9_FRONTLIGHT=y`
   for Android bring-up, or `m` if the vendor loads the module. Dependencies:
   `I2C`, `OF`, `GPIOLIB`, `NEW_LEDS`, `LEDS_CLASS`.
2. Review `patches/0002-arm64-a9-frontlight-PROPOSAL.patch`. It adds
   `a9-frontlight.dtsi` beside the A9 board DTS and includes it at the end of
   `sm6115-hisense-hlte556n.dts`. The fragment merges the existing disabled
   placeholders rather than creating duplicate nodes. Android currently
   carries a synchronized inline copy checked by `tests/test-dt.py` and
   `snapshot-kernel.sh`. Both enable AW and leave optional KTD disabled.
3. Reconcile `gpio-reserved-ranges` against your current board. This proposal
   reopens **only 4, 5, 83, 85** relative to the repository board: QUP1 SDA/SCL
   plus the two AW HWENs. GPIO88/101 remain reserved. Keep subsequent pin
   decisions. Leave `sdhc_2` disabled. No HWEN pinctrl state
   may force an initial output level. In particular, do not copy the stock
   AW#1 `pinctrl-0` blindly: it references PMI632's analog top-thermistor pin,
   not TLMM83.
4. Keep the vendor's own compat service and its four configurable LED paths.
   It currently runs as root, so the default root-writable LED attributes need
   no DAC permission expansion. Enforcing SELinux still needs **two narrow
   real-device brightness labels** granting the shim access; capture the actual
   paths with the read-only command in the document. Do not label an entire
   I2C controller. The existing compat policy declares
   `vendor_sysfs_a9_frontlight` but intentionally has no guessed path entries.

These are review proposals, not patches applied to any shared source/build or
to `scripts/mainline/kernel/`. To regenerate after editing the standalone
sources, run `python3 scripts/mainline/frontlight/make-proposals.py`; it reads
the local 7.2.10 LED Kconfig/Makefile and repository board DTS and writes only
these two proposal files.

## Offline checks

From the repository root:

```sh
nix-shell scripts/mainline/frontlight/shell.nix --run \
  'make -C scripts/mainline/frontlight BUILD=build/host test'
nix-shell scripts/mainline/frontlight/shell.nix --run \
  'make -C scripts/mainline/frontlight BUILD=build/sanitize CFLAGS="-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer" test'
nix-shell scripts/mainline/frontlight/shell.nix --run \
  'python3 scripts/mainline/frontlight/tests/test-dt.py'
nix-shell scripts/mainline/frontlight/shell.nix --run \
  'python3 scripts/mainline/frontlight/offline-build.py'
```

The build script reads `~/A9-build/mainline/linux-7.2.10` and the Android build's
`.config`/`Module.symvers`. Its private `O=` is
`~/A9-build/mainline/sol-frontlight-7.2.10/kernel`; external module objects go in
the sibling `module/`. It runs `olddefconfig`, `modules_prepare`, then external
`W=1 modules` with GCC 14.3.0. It never loads the result or creates an image.
The copied symbol CRCs enable modpost; this is a compile/link check, **not** a
claim that the module matches a future coordinator kernel. Rebuild in the final
kernel tree for integration. Nix sets `CC` to the cross compiler, so host tests
deliberately use `HOSTCC=gcc`.

Host tests execute the production register core with fake I2C/GPIOs: all 2048
levels, no writes on probe, exact ratio split/order, stock current/OVP setup
before enabling, unsafe live-profile rejection, cold restart and injected
failures at every initialization/brightness transaction. DTS tests compile the
full board and Android DT, assert AW enabled/KTD disabled, GPIO88/101 reserved,
the four mappings, exact pin-reservation change and inline-block/hash sync.
Adoption regressions inject `ENXIO`, `EIO` and `ETIMEDOUT` at all seven reads
for each chip and require zero register writes and zero GPIO calls on failure.

Binding validation (with a working `dtschema` installation): stage the binding
under `Documentation/devicetree/bindings/leds/`, alongside upstream `common.yaml`,
then run `dt-doc-validate`, `dt-mk-schema -j` and `dt-validate -l
hisense,a9-frontlight` against `build/dt/frontlight.dtb`. Staging is necessary
because `$id` path validation expects the kernel binding layout. This session
used an isolated Python venv because nixpkgs' dtschema 2025.08 package has a
broken jsonschema dependency constraint.

LED sysfs setters run on the LED core workqueue. A successful sysfs write
acknowledges queuing, not I2C success; errors are logged and the driver switches
off that chip. The compat daemon currently caches successful sysfs writes, so
it will retry a failed chip only after the property changes or its process
restarts. Improving that recovery requires a future shim change; no vendor or
compat source is changed by this proposal.
