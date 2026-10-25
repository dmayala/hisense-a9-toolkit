#!/usr/bin/env python3
"""Generate a proposal against the coordinator's current kernel/ integration.
Never edits kernel/ or the shared Linux tree. Usage: script [linux-7.2.10].
For 0007/0008-only bases, proposes the initial observe integration. For the
coordinator's integrated 0009 base, adds only the new stock-health Linux delta.
"""
import difflib
import pathlib
import re
import sys

here = pathlib.Path(__file__).resolve().parent
repo = here.parents[2]
src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path.home() / 'A9-build/mainline/linux-7.2.10'
changes = []


def diff(path, old, new):
    return ('diff --git a/' + path + ' b/' + path + '\n' +
            ('new file mode 100644\n' if not old else '') +
            ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       fromfile='a/' + path if old else '/dev/null',
                                       tofile='b/' + path)))


def change(path, old, new):
    assert old != new, path
    changes.append(diff(path, old, new))


# Build a SMALL Linux delta on top of the coordinator's existing 0008.
kpath = 'drivers/power/supply/Kconfig'
k = (src / kpath).read_text()
assert 'config CHARGER_PMI632_A9\n' in k
if 'config CHARGER_PMI632_A9_TELEMETRY_ONLY\n' in k:
    # The coordinator has integrated 0009. Do not rewrite applied patches or
    # re-enable/reconfigure the node; add a small follow-up to that baseline.
    body = ''
    for name in ['pmi632-a9.c', 'pmi632-policy.h', 'pmi632-fv.h']:
        path = 'drivers/power/supply/' + name
        old, new = (src / path).read_text(), (here / name).read_text()
        if old != new:
            body += diff(path, old, new)
    assert body, 'No pending driver delta against the integrated kernel'
    inner = ('From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n'
             'From: PMI632 proposal <pmic@example.invalid>\n'
             'Date: Thu, 15 Oct 2026 00:00:00 +0000\n'
             'Subject: [PATCH] power: supply: qualify A9 OV health like stock\n\n'
             'Apply after integrated 0009. Match the stock battery-health FV +\n'
             '40mV qualification. Constant QG codes in observe cannot establish\n'
             'stale hardware. Leave the probe-only FV write budget unchanged.\n\n' + body)
    patch_dir = repo / 'scripts/mainline/kernel/patches'
    numbers = [int(p.name.split('-', 1)[0]) for p in patch_dir.glob('[0-9]*.patch')]
    number = max(numbers, default=9) + 1
    name = f'{number:04d}-power-supply-pmi632-stock-health.patch'
    change('scripts/mainline/kernel/patches/' + name, '', inner)
    (here / 'kernel-telemetry-PROPOSAL.patch').write_text(
        'Subject: [PROPOSAL] mainline: qualify PMI632 battery OV health like stock\n\n'
        'Apply to the A9 project, not Linux. Current base already integrates\n'
        '0007-0009, built-in observe/FV config and the enabled board node.\n'
        f'Only adds Linux patch {number:04d}; does not edit applied 0009, build or config.\n'
        'No raw capture or device identifier is included. This is unapplied.\n\n' + ''.join(changes))
    sys.exit(0)
assert 'config CHARGER_PMI632_A9_TELEMETRY_ONLY\n' not in k
kn = k.replace('endif # POWER_SUPPLY', '''config CHARGER_PMI632_A9_TELEMETRY_ONLY
	bool "Permanently disable A9 charging enable path"
	depends on CHARGER_PMI632_A9
	help
	  Compile out control-mode configuration and enable paths even when the
	  module parameter and DT validation flag request charging. Observe
	  mode is the default; the optional probe-only stock FV write is allowed.

config CHARGER_PMI632_A9_OBSERVE
	bool "Observe firmware charging with optional probe-only stock FV"
	depends on CHARGER_PMI632_A9
	default y if CHARGER_PMI632_A9_TELEMETRY_ONLY
	help
	  Exclude charger/QG control writes except optional fv_stock at probe.
	  With fv_stock=0 firmware charger configuration stays untouched.
	  Temperature uses only normal upstream ADC5 conversions (which write
	  ADC registers); observe_adc=0 omits these and makes TEMP unavailable.

config CHARGER_PMI632_A9_FV_STOCK
	bool "Default fv_stock=1: one probe-only stock 4.450 V float write"
	depends on CHARGER_PMI632_A9_OBSERVE
	help
	  Default the read-only load-time fv_stock parameter to 1. The telemetry
	  image explicitly selects this option; pure observe defaults to 0.
	  After all probe dependencies, allow one write of 0x55 to 0x1070 and
	  verify readback. Unexpected handoff/readback or I/O errors latch health
	  failure without retry, further writes, or charging-disable operations.
	  fv_stock=0 still selects pure observe, even with this option enabled.

endif # POWER_SUPPLY''')
cpath = 'drivers/power/supply/pmi632-a9.c'
old_driver = (src / cpath).read_text()
new_driver = (here / 'pmi632-a9.c').read_text()
assert old_driver != new_driver
inner = ('From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n'
         'From: PMI632 proposal <pmic@example.invalid>\n'
         'Date: Thu, 15 Oct 2026 00:00:00 +0000\n'
         'Subject: [PATCH] power: supply: observe A9 with one stock FV probe write\n\n'
         'Apply after the existing kernel patch 0008. fv_stock permits one probe-only\n'
         'stock FV write and readback; no retries. ADC uses the upstream path, unless\n'
         'observe_adc=0 is selected to omit ADC requests too.\n\n' +
         diff(kpath, k, kn) + diff(cpath, old_driver, new_driver) +
         diff('drivers/power/supply/pmi632-policy.h',
              (src / 'drivers/power/supply/pmi632-policy.h').read_text(),
              (here / 'pmi632-policy.h').read_text()) +
         diff('drivers/power/supply/pmi632-fv.h', '', (here / 'pmi632-fv.h').read_text()))
change('scripts/mainline/kernel/patches/0009-power-supply-pmi632-telemetry-only.patch', '', inner)

path = 'scripts/mainline/kernel/build.sh'
s = (repo / path).read_text()
anchor = '\tcp "$REPO/scripts/mainline/pmic/a9-pmi632.dtsi" "$SRC/arch/arm64/boot/dts/qcom/"\n'
assert s.count(anchor) == 1
# A commented include is not active. Ensure one include in the PRIVATE copy.
new = s.replace(anchor, anchor + '''	if ! grep -qx '#include "a9-pmi632.dtsi"' "$SRC/arch/arm64/boot/dts/qcom/$DTS.dts"; then
		echo '#include "a9-pmi632.dtsi"' >> "$SRC/arch/arm64/boot/dts/qcom/$DTS.dts"
	fi
	cat >> "$SRC/arch/arm64/boot/dts/qcom/$DTS.dts" <<'PMIC_DTS'
/* Telemetry: observe with one probe-only stock FV write (fv_stock=1). */
&a9_pmi632_power {
	status = "okay";
	/delete-property/ hisense,charging-validated;
};
PMIC_DTS
''')
# Existing initramfs closure requests this module; remove it when built in.
new = new.replace('panel-hisense-hlte556n-eink pmi632-a9 <<', 'panel-hisense-hlte556n-eink <<')
anchor = '\tlocal rc=0\n'
assert new.count(anchor) == 1
new = new.replace(anchor, anchor + '''	if grep -qx 'CONFIG_CHARGER_PMI632_A9=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_OBSERVE=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_FV_STOCK=y' "$OBJ/.config"; then
		echo "  ok   PMI632 built in, observe with one stock-FV probe write"
	else
		echo "  FAIL PMI632 observe configuration"; rc=1
	fi
	local pmic_status
	pmic_status=$(nixrun "fdtget -t s '$OUT/$DTS.dtb' '/soc@0/spmi@1c40000/pmic@2/charger@1000' status") || rc=1
	[ "$pmic_status" = okay ] || { echo "  FAIL PMI632 telemetry DT node disabled/missing"; rc=1; }
''')
change(path, s, new)

path = 'scripts/mainline/kernel/a9.config'
s = (repo / path).read_text()
new = s.replace('# PMI632 battery/charger (sol-pmic proposal): module, DT node disabled',
                '# PMI632 telemetry proposal: built in, observe firmware charging')
new = re.sub(r'^CONFIG_CHARGER_PMI632_A9(?:_TELEMETRY_ONLY|_OBSERVE|_FV_STOCK)?=.*\n', '', new, flags=re.M)
new += '\nCONFIG_CHARGER_PMI632_A9=y\nCONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y\nCONFIG_CHARGER_PMI632_A9_OBSERVE=y\nCONFIG_CHARGER_PMI632_A9_FV_STOCK=y\n'
change(path, s, new)

(here / 'kernel-telemetry-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL] mainline: integrate PMI632 observe telemetry with one stock FV write\n\n'
    'Apply to the A9 project, not the Linux tree. Based on current working\n'
    'kernel integration: patches 0007/0008 and disabled/commented board include.\n'
    'Adds kernel patch 0009; do NOT additionally apply pmic patches 0001-0003.\n'
    'Other agents own kernel/; this file has NOT been applied there.\n'
    'No capture contents or device identifiers are included.\n\n' + ''.join(changes))
