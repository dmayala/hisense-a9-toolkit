#!/usr/bin/env python3
"""Generate review-only project patches; never edits kernel/ or shared Linux."""
import difflib
import pathlib

here = pathlib.Path(__file__).resolve().parent
repo = here.parents[2]


def diff(path, old, new):
    return ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       fromfile='a/' + path if old else '/dev/null', tofile='b/' + path))


# Apply only in a private Linux board-DTS copy, not the A9 project.
fragment = (here / 'a9-smp-c1.dtsi').read_text()
(here / '0001-stage-C1-cpu-only-OSI-DTS-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL] arm64: dts: A9 C1 CPU-only OSI before cluster collapse\n\n'
    'Linux-tree fragment only; include LAST in the private A9 board DTS.\n'
    'Needs stage-c1.config and the C1 command line. Not for A/B.\n\n' +
    diff('arch/arm64/boot/dts/qcom/a9-smp-c1.dtsi', '', fragment))
path = 'scripts/mainline/kernel/initramfs/a9-ctl'
old = (repo / path).read_text()
anchor = 'collect_safe() {\n\td=$1\n'
assert old.count(anchor) == 1
addition = (here / 'capture-addition.sh').read_text().splitlines(True)[2:]
new = old.replace(anchor, anchor + ''.join('\t' + line for line in addition))
(here / '0002-capture-cpu-telemetry-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL] mainline: capture SMP, cpufreq, idle and boot continuity\n\n'
    'A9-project patch for coordinator review; NOT applied to kernel/.\n'
    'Read only sysfs/proc metadata; boot-id/cmdline capture stays outside git.\n\n' + diff(path, old, new))
path = 'scripts/mainline/android/vendor/bin/a9ml-ctl.sh'
old = (repo / path).read_text()
anchor = '\tdmesg > $D/dmesg.txt 2>&1\n'
assert old.count(anchor) == 1
extra = ('\tuname -a > "$D/uname.txt"\n'
         '\tcat /proc/uptime >> "$D/uname.txt"\n'
         '\tcat /proc/interrupts > "$D/interrupts.txt"\n\td=$D\n')
new = old.replace(anchor, anchor + extra + ''.join('\t' + line for line in addition))
(here / '0003-android-capture-cpu-telemetry-PROPOSAL.patch').write_text(
    'Subject: [PROPOSAL] Android: capture the same SMP/DVFS/idle evidence over NCM\n\n'
    'A9-project patch for Android owner review; NOT applied to android/.\n'
    'Same read-only evidence as diskless capture, without a new transport.\n\n' + diff(path, old, new))
