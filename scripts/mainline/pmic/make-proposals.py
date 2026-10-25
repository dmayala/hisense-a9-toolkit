#!/usr/bin/env python3
"""Regenerate proposal patches from canonical local sources. Offline only.
Usage: python3 -I scripts/mainline/pmic/make-proposals.py /path/to/linux-7.2.10
Does not edit the kernel tree. 0001 is maintained separately.
"""
import difflib
import pathlib
import re
import sys
import subprocess

here = pathlib.Path(__file__).resolve().parent
src = pathlib.Path(sys.argv[1]).resolve()
repo = here.parents[2]


def diff(path, before, after):
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       fromfile='a/' + path if before else '/dev/null',
                                       tofile='b/' + path))


kpath = 'drivers/power/supply/Kconfig'
mpath = 'drivers/power/supply/Makefile'
# Pin base files: the shared working tree may already contain this proposal.
k = subprocess.check_output(['git', '-C', str(src), 'show', 'v7.2.10:' + kpath], text=True)
m = subprocess.check_output(['git', '-C', str(src), 'show', 'v7.2.10:' + mpath], text=True)
kn = k + '''
config CHARGER_PMI632_A9
	tristate "Hisense A9 PMI632 experimental battery/charger"
	depends on OF && MFD_SPMI_PMIC && IIO
	select REGMAP
	help
	  Board-specific bring-up proposal. Control defaults OFF and needs
	  both allow_charging=1 and the coordinator's DT validation property.
	  Observe leaves firmware charging untouched instead. See its option.
	  Battery capacity is an uncalibrated voltage estimate. See the A9
	  project's docs/mainline-pmic.md before binding this driver.

config CHARGER_PMI632_A9_TELEMETRY_ONLY
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
'''
# Kconfig symbol must be within the POWER_SUPPLY conditional.
assert k.rstrip().endswith('endif # POWER_SUPPLY')
kn = k[:k.rfind('endif # POWER_SUPPLY')] + kn[len(k):] + '\nendif # POWER_SUPPLY\n'
body = diff(kpath, k, kn) + diff(mpath, m, m + '\nobj-$(CONFIG_CHARGER_PMI632_A9) += pmi632-a9.o\n')
for name in ['pmi632-a9.c', 'pmi632-policy.h', 'pmi632-fv.h']:
    body += diff('drivers/power/supply/' + name, '', (here / name).read_text())
(here / 'patches/0002-power-supply-a9-pmi632-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL 2/3] power: supply: add guarded A9 PMI632 bring-up driver\n\n'
    'Offline compiled only. No hardware validation; default charging OFF.\n'
    'Canonical source and policy tests live in scripts/mainline/pmic/.\n\n' + body)

board = 'sm6115-hisense-hlte556n.dts'
board_text = (repo / 'docs/mainline' / board).read_text()
body = diff('arch/arm64/boot/dts/qcom/a9-pmi632.dtsi', '', (here / 'a9-pmi632.dtsi').read_text())
if not re.search(r'^#include\s+"a9-pmi632.dtsi"\s*$', board_text, re.M):
    body += diff('arch/arm64/boot/dts/qcom/' + board, board_text,
                 board_text + '\n/* Experimental power proposal, node disabled by default. */\n#include "a9-pmi632.dtsi"\n')
(here / 'patches/0003-arm64-a9-pmi632-dts-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL 3/3] arm64: dts: qcom: add disabled A9 power proposal\n\n'
    'Does not enable charging or supply the validation gate.\n'
    'Apply after copying the project board DTS into the kernel tree.\n\n' + body)
