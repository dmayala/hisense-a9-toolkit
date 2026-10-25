#!/usr/bin/env python3
"""Run the actual staged controller against a temporary fake sysfs/commands.

No device transport exists in this test. Every live-store path in the copied
controller is replaced by a temporary regular file before shell execution.
The production script has no test-mode or path-override escape hatch.
"""
import importlib.util
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest

REPO = Path(__file__).resolve().parents[2]
HERE = REPO / 'scripts/mainline/wifi'
# CPU emulation is much slower than the target. Scale host harness deadlines
# only; production monotonic deadlines and their fixture values stay intact.
TIMEOUT_SCALE = float(os.environ.get('A9_WIFI_TEST_TIMEOUT_SCALE', '1'))


def valid_trace():
    return '''123 execve("/vendor/bin/a9-wifi-sandbox", ["sandbox", "rmtfs"], 0) = 0
123 openat(AT_FDCWD, "/vendor/etc/a9-wifi/assets.sha256", O_RDONLY|O_CLOEXEC|O_NOFOLLOW) = 3</vendor/etc/a9-wifi/assets.sha256>
123 pivot_root(".", ".old") = 0
123 umount2("/.old", MNT_DETACH) = 0
123 openat(AT_FDCWD, "modemst1", O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = 65</mnt/a9-wifi/rmtfs/modemst1>
123 prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, 0x123) = 0
123 execveat(30</vendor/bin/rmtfs>, "", ["rmtfs"], 0x456, AT_EMPTY_PATH) = 0
'''


class TraceAudit(unittest.TestCase):
    def audit(self, extra='', replacement=None):
        text = valid_trace()
        if replacement:
            text = text.replace(*replacement)
        return subprocess.run(['awk', '-f', str(HERE / 'audit-opens.awk')], input=text + extra,
                              capture_output=True, text=True)

    def test_signal_delivery_lines_do_not_disturb_the_audit(self):
        sig = '123 --- SIGUSR1 {si_signo=SIGUSR1, si_code=SI_USER, si_pid=777, si_uid=0} ---\n'
        self.assertEqual(self.audit(extra=sig).returncode, 0)

    def test_valid_preparation_and_exec(self):
        self.assertEqual(self.audit().returncode, 0)

    def test_actual_strace_annotations_padding_and_bionic_startup(self):
        self.assertEqual(self.audit(replacement=('AT_FDCWD,', 'AT_FDCWD</>,')).returncode, 0)
        self.assertEqual(self.audit(replacement=('MNT_DETACH) =', 'MNT_DETACH)      =')).returncode, 0)
        self.assertEqual(self.audit(replacement=('/vendor/etc/a9-wifi/assets.sha256', '/dev/null'),
                                    extra='123 openat(AT_FDCWD</>, "/dev/__properties__", O_RDONLY) = -1 EPERM (Operation not permitted)\n').returncode, 0)
        for name in ('property_info', 'properties_serial', 'u:object_r:debug_prop:s0',
                     'u:object_r:arm64_memtag_prop:s0', 'u:object_r:device_config_memory_safety_native_prop:s0',
                     'u:object_r:build_prop:s0'):
            with self.subTest(name=name):
                path = '/dev/__properties__/' + name
                self.assertEqual(self.audit(replacement=('/vendor/etc/a9-wifi/assets.sha256', path)).returncode, 0)
                self.assertNotEqual(self.audit('123 openat(AT_FDCWD</>, "' + path + '", O_RDONLY) = 3<' + path + '>\n').returncode, 0)
        self.assertNotEqual(self.audit(replacement=('/vendor/etc/a9-wifi/assets.sha256', '/dev/__properties__/unreviewed')).returncode, 0)
        self.assertNotEqual(self.audit(replacement=('AT_FDCWD,', '77</>,')).returncode, 0)
        self.assertNotEqual(self.audit(replacement=('AT_FDCWD,', 'AT_FDCWD</other>,')).returncode, 0)

    def test_nested_character_device_annotation(self):
        self.assertEqual(self.audit(replacement=('3</vendor/etc/a9-wifi/assets.sha256>', '3</dev/null<char 1:3>>')).returncode, 0)

    def test_real_partition_and_disk_alias_are_rejected(self):
        for line in ['openat(AT_FDCWD, "/dev/block/by-name/modemst1", O_RDONLY) = 3</dev/block/sda8>',
                     'fstat(77, {st_mode=S_IFBLK|0600, st_rdev=makedev(8, 0), ...}) = 0']:
            self.assertNotEqual(self.audit('123 ' + line + '\n').returncode, 0)

    def test_late_successful_open_is_rejected_even_known_asset(self):
        self.assertNotEqual(self.audit('123 open("/dev/null", O_RDONLY) = 3</dev/null>\n').returncode, 0)

    def test_missing_boundary_and_truncated_path_are_rejected(self):
        self.assertNotEqual(self.audit(replacement=('PR_SET_SECCOMP', 'PR_NOT_A_FILTER')).returncode, 0)
        self.assertNotEqual(self.audit(replacement=('"modemst1",', '"modemst1"...,')).returncode, 0)

    def test_escaped_alias_and_write_to_vendor_are_rejected(self):
        self.assertNotEqual(self.audit(replacement=('O_RDONLY|O_CLOEXEC', 'O_RDWR|O_CLOEXEC')).returncode, 0)
        self.assertNotEqual(self.audit(replacement=('"modemst1",', '"modem\\x73t1",')).returncode, 0)


class Controller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a9-wifi-controller-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.d = self.root / 'mnt/a9-wifi/trial'
        self.d.mkdir(parents=True)
        self.dev = self.root / 'sys/devices/platform/soc/6080000.remoteproc'
        self.dev.mkdir(parents=True)
        self.of = self.dev / 'of_node'
        self.of.mkdir()
        (self.of / 'compatible').write_bytes(b'qcom,sm6115-mpss-pas\0')
        edge = self.of / 'glink-edge'
        edge.mkdir();(edge / 'label').write_bytes(b'mpss\0')
        (edge / 'qcom,remote-pid').write_bytes(b'\0\0\0\1')
        self.rpmsg = self.root / 'sys/bus/rpmsg/devices'
        self.rpmsg.mkdir(parents=True)
        driver = self.root / 'sys/bus/rpmsg/drivers/qcom_smd_qrtr'
        driver.mkdir(parents=True)
        transport = self.dev / '6080000.remoteproc:glink-edge'
        transport.mkdir();(transport / 'of_node').symlink_to(edge)
        self.ipcrtr = transport / '6080000.remoteproc.IPCRTR.-1.-1'
        self.ipcrtr.mkdir();(self.ipcrtr / 'driver').symlink_to(driver)
        self.bus_link = self.rpmsg / self.ipcrtr.name
        self.driver_link = driver / self.ipcrtr.name
        (self.d / 'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
        (self.d / 'rmtfs.admission').chmod(0o600)
        remote = self.root / 'sys/class/remoteproc/remoteproc47'
        remote.mkdir(parents=True)
        (remote / 'device').symlink_to(self.dev)
        (remote / 'firmware').write_text('qcom/sm6115/hisense/hlte556n/modem.mdt\n')
        (remote / 'state').write_text('running\n')
        self.state = remote / 'state'
        (remote / 'recovery').write_text('disabled\n')
        (self.root / 'uptime').write_text('0.0 0.0\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for cmd in ('setprop', 'reboot-fixture'):
            script = self.bin / cmd
            script.write_text('#!' + shutil.which('sh') + '\nprintf "%s\\n" "' + cmd + ' $*" >> "$FIXTURE_COMMAND_LOG"\n')
            script.chmod(0o700)
        self.commandlog = self.root / 'commands'
        lookup = self.bin / 'qrtr-lookup-fixture'
        lookup.write_text('#!' + shutil.which('sh') + '''
case "${1:-}" in
"") printf '  Service Version Instance Node  Port\n14 1 0 7 14\n4097 N/A 0 7 12289\n4097 N/A 2 7 12290\n4097 N/A 4 7 12291\n';;
4097) printf '4097 N/A 0 7 12289\n4097 N/A 2 7 12290\n4097 N/A 4 7 12291\n';;
64|69) echo "$1 1 0 0 12345";;
esac
''')
        lookup.chmod(0o700)
        if TIMEOUT_SCALE != 1:
            # Emulation scales external wall time only. Controller uptime /
            # admission / guard / cleanup limits remain production values.
            timer = self.bin / 'timeout'
            timer.write_text('#!' + shutil.which('python3') + '\n' +
                             'import os, sys\na=sys.argv[1:]\n' +
                             "i=1 if a and a[0]=='--foreground' else 0; i+=2 if a[i:i+2]==['-s','KILL'] else 0\n" +
                             'a[i]=str(float(a[i])*' + str(TIMEOUT_SCALE) + ')\n' +
                             'os.execv(' + repr(shutil.which('timeout')) + ', [\'timeout\']+a)\n')
            timer.chmod(0o700)
        text = (HERE / 'coordinator-target.sh').read_text()
        for old, new in [('/mnt/a9-wifi', str(self.root / 'mnt/a9-wifi')),
                         ('/sys/class/remoteproc', str(self.root / 'sys/class/remoteproc')),
                         ('/sys/class/block', str(self.root / 'sys/class/block')),
                         ('/sys/bus/rpmsg', str(self.root / 'sys/bus/rpmsg')),
                         ('/sys/module/', str(self.root / 'sys/module') + '/'),
                         ('/vendor/bin/qrtr-lookup', str(lookup)),
                         ('/dev/block/by-name', str(self.root / 'dev/block/by-name')),
                         ('/dev/kmsg', str(self.root / 'kmsg')),
                         ('/proc/uptime', str(self.root / 'uptime')),
                         ('/system/bin/reboot', 'reboot-fixture'), ('/system/bin/sh', shutil.which('sh'))]:
            text = text.replace(old, new)
        self.assertNotIn('if="/dev/block/by-name/', text)
        # Functions emulate only phone identity and accelerated sleeping. Real
        # timeout, shell, awk, dd, hashing and controller state logic execute.
        fake = '''
id() { echo 0; }
getprop() { case "$1" in ro.a9ml.vendor|ro.a9ml.wifi) echo 1;; ro.boot.slot_suffix) echo _b;; ro.a9ml.wifi.pd_mapper) echo kernel;; esac; }
uname() { echo 7.2.fixture; }
sleep() { command sleep 0.01; }
readlink() { case "$1" in "/proc/$FIXTURE_TRACER_PID/exe") echo "$FIXTURE_TRACE_PATH";; *) command readlink "$@";; esac; }
stat() {
    case "$*" in
        '-c %u:%a:%h:%s '*) shift 2; printf '0:%s\\n' "$(command stat -c '%a:%h:%s' "$1")"; return;;
        '-f -c %T '*rmtfs.admission) echo tmpfs; return;;
    esac
    if [ "$#" = 4 ] && [ "$1" = -L ] && [ "$2" = -c ] && [ "$3" = '%F' ]; then
        case "$4" in "$FIXTURE_BLOCK_ROOT"/*) echo "${FIXTURE_BLOCK_KIND:-block special file}"; return;; esac
    fi
    output=$(command stat "$@") || return
    if [ "${FIXTURE_STAT_STYLE:-gnu}" = toybox ]; then
        printf '%s\\n' "$output" | sed -e 's/block special file/block device/g' -e 's/character special file/character device/g' -e 's/^fifo$/FIFO (named pipe)/'
    else printf '%s\\n' "$output"; fi
}
'''
        text = text.replace('set -eu\n', 'set -eu\n' + fake, 1)
        self.script = self.root / 'mnt/a9-wifi/tools/coordinator-target.sh'
        self.script.parent.mkdir(parents=True)
        self.script.write_text(text)
        self.env = os.environ | {'PATH': str(self.bin) + ':' + os.environ['PATH'],
                                  'FIXTURE_COMMAND_LOG': str(self.commandlog),
                                  'FIXTURE_TRACER_PID': str(os.getpid()),
                                  'FIXTURE_TRACE_PATH': str(self.script.parent / 'strace'),
                                  'FIXTURE_BLOCK_ROOT': str(self.root / 'dev/block/by-name')}
        self.roles = ('rmtfs', 'tqftpserv', 'diag-router')
        for role in self.roles:
            (self.d / (role + '.tracer')).write_text(str(os.getpid()))
            (self.d / (role + '.log')).write_text('A9_WIFI_READY ' + role + '\n')
        (self.d / 'diag-router.log').write_text('A9_WIFI_READY diag-router\nA9_WIFI_LOCAL_NODE 7\n')
        self.running = True
        self.stuck = False
        self.allow_start = False
        fw = self.root / 'sys/module/firmware_class/parameters/path'
        fw.parent.mkdir(parents=True)
        firmware = self.root / 'mnt/a9-wifi/firmware'
        firmware.mkdir()
        fw.write_text(str(firmware)+'\n')
        modem_fw = self.root / 'vendor/firmware/qcom/sm6115/hisense/hlte556n/modem.mdt'
        modem_fw.parent.mkdir(parents=True); modem_fw.write_bytes(b'synthetic modem firmware\n')
        (firmware/'qcom').symlink_to(self.root/'vendor/firmware/qcom')

        def emulate_sysfs():
            while self.running:
                try:
                    # Atomic replace: a concurrent reader never sees a truncated file.
                    def put(text):
                        tmp = self.state.with_name('state.tmp'); tmp.write_text(text); os.replace(tmp, self.state)
                    if self.state.read_text().strip() == 'stop' and not self.stuck:
                        put('offline\n')
                    elif self.state.read_text().strip() == 'start' and self.allow_start:
                        self.set_ipcrtr(True)
                        put('running\n')
                    if self.state.read_text().strip() == 'offline':
                        self.set_ipcrtr(False)
                except OSError:
                    pass
                time.sleep(0.002)
        self.thread = threading.Thread(target=emulate_sysfs, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def set_ipcrtr(self, active):
        for path in (self.bus_link, self.driver_link):
            if active and not path.is_symlink(): path.symlink_to(self.ipcrtr)
            if not active and path.is_symlink(): path.unlink()

    def shutdown(self):
        self.running = False
        self.thread.join()

    def run_stage(self, *args, timeout=4):
        return subprocess.run([shutil.which('sh'), str(self.script), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout * TIMEOUT_SCALE)

    def function_probe(self, tail, functions=None):
        probe = self.root / 'function-probe.sh'
        probe.write_text((functions or self.script.read_text().split('case "${1:-}" in')[0]) + '\n' + tail)
        return subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True, timeout=8 * TIMEOUT_SCALE)

    def test_ppid_parser_handles_comm_delimiters_without_proc_children(self):
        proc = self.root / 'proc'
        (proc / '123').mkdir(parents=True)
        (proc / '456').mkdir()
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/proc/', str(proc) + '/')
        for comm in ('rmtfs', 'space in name', 'odd ) S 999 ( name', 'line\nbreak)'):
            with self.subTest(comm=comm):
                (proc / '456/stat').write_text('456 (' + comm + ') S 123 ' + '0 ' * 17 + '9999999999\n')
                (proc / '123/stat').write_text('123 (strace) S 1 ' + '0 ' * 17 + '12345\n')
                result = self.function_probe('process_record 456\ntrace_children 123\n', functions)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '123 9999999999 S\n456\n')
        (proc / '456/stat').write_text('456 (broken) S nope\n')
        self.assertNotEqual(self.function_probe('trace_children 123\n', functions).returncode, 0)

    def test_proc_census_uses_builtin_reads_and_handles_disappearance_quietly(self):
        proc = self.root / 'proc'
        for pid in (123, 456, 789):
            (proc / str(pid)).mkdir(parents=True)
        for pid in (123, 456):
            (proc / str(pid) / 'stat').write_text(f'{pid} (name) S 123 ' + '0 ' * 17 + '42\n')
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/proc/', str(proc) + '/')
        native_functions = functions.replace('read_process_record() {', 'native_read_process_record() {', 1)
        hooks = '''
process_record() { echo forbidden-per-pid-subshell >&2; return 99; }
read_process_record() {
    [ "$1" != 789 ] || rmdir "PROC/789"
    native_read_process_record "$1"
}
trace_children 123
'''.replace('PROC', str(proc))
        result = self.function_probe(hooks, native_functions)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '123\n456\n')
        self.assertNotIn("can't open", result.stderr)
        self.assertNotIn('No such file', result.stderr)
        # A live but unreadable/malformed entry remains a terminal failure.
        (proc / '456/stat').write_text('broken\n')
        self.assertNotEqual(self.function_probe('trace_children 123\n', functions).returncode, 0)
        result = self.function_probe('process_record 999\n', functions)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("can't open", result.stderr)
        self.assertNotIn('No such file', result.stderr)

    def test_native_stat_inventory_format_and_missing_object_fail_closed(self):
        # Real host stat, or the actual Android Toybox stat under QEMU; no stat
        # fixture hook. Exercise multi-operand formatting, empty and nonempty
        # files and a symlink, then a disappearing descriptor target.
        base = self.root / 'native-fds'
        base.mkdir()
        (base / '10').write_text('abc')
        (base / '11').touch()
        (base / '12').symlink_to(base / '10')
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        result = self.function_probe('''
fd_inventory=$(command stat -L -c '%n|%F|%s|%d:%i' BASE/*) || exit 1
fd_record BASE/10
[ "$fd_kind:$fd_size" = 'regular file:3' ] || exit 1
first_identity=$fd_identity
fd_record BASE/12
[ "$fd_identity" = "$first_identity" ] || exit 1
fd_record BASE/11
case "$fd_kind:$fd_size" in 'regular file:0'|'regular empty file:0') ;; *) exit 1;; esac
! fd_record BASE/99 || exit 1
'''.replace('BASE', str(base)), functions)
        self.assertEqual(result.returncode, 0, result.stderr)
        (base / '10').unlink()
        result = self.function_probe("fd_inventory=$(command stat -L -c '%n|%F|%s|%d:%i' " + str(base) + "/*) || exit 17\n", functions)
        self.assertEqual(result.returncode, 17)

    def test_descriptor_failure_classification_keeps_complete_inventory_required(self):
        proc = self.root / 'inventory-proc'
        fds = proc / '123/fd'; fds.mkdir(parents=True)
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/proc/',str(proc)+'/')
        prefix = 'kmsg_note() { printf "%s\\n" "$*"; }\nrole=tqftpserv\npid=123\n'
        (fds/'20').write_text('fixture')
        success = self.function_probe(prefix+'descriptor_inventory\n',functions)
        self.assertEqual(success.returncode,0,success.stderr)
        (fds/'21').symlink_to(fds/'missing')
        vanished = self.function_probe(prefix+'descriptor_inventory\n',functions)
        self.assertNotEqual(vanished.returncode,0)
        self.assertIn('error_class=2',vanished.stdout)
        for text,code,want in [('Permission denied',1,13),('unrecognized failure',2,0)]:
            source = prefix+'stat() { echo "PRIVATE_TEST_PID '+text+'" >&2; return '+str(code)+'; }\ndescriptor_inventory\n'
            result = self.function_probe(source,functions)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('error_class='+str(want),result.stdout)
            self.assertNotIn('PRIVATE_TEST_PID',result.stdout+result.stderr)
        (fds/'20').unlink(); (fds/'21').unlink()
        empty = self.function_probe(prefix+'descriptor_inventory\n',functions)
        self.assertNotEqual(empty.returncode,0)
        self.assertIn('error_class=2',empty.stdout)

    def test_static_topology_before_arming_and_after_start_without_modem_diag_row(self):
        self.state.write_text('offline\n');self.set_ipcrtr(False)
        self.assertEqual(self.function_probe('topology before\n').returncode, 0)
        self.state.write_text('running\n');self.set_ipcrtr(True)
        self.assertEqual(self.function_probe('topology after\n').returncode, 0)
        rows=(self.d / 'topology-after.qrtr').read_text()
        self.assertNotIn('4097 N/A 1 0', rows)  # no modem CMD1 dependency
        other=self.root / 'sys/class/remoteproc/remoteproc9';other.mkdir()
        (other / 'state').write_text('running\n')
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        (other / 'state').write_text('offline\n')
        self.assertEqual(self.function_probe('topology after\n').returncode, 0)
        (other / 'state').unlink()
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)

    def test_adsp_sensors_topology_and_release_preserve_mpss_identity(self):
        # Same fake sysfs as the MPSS tests, with the kernel's named ADSP and
        # LPASS edge. QRTR node numbers are synthetic and independent of SMEM.
        dev = self.root / 'sys/devices/platform/soc/a400000.remoteproc'
        edge = dev / 'of_node/glink-edge'
        edge.mkdir(parents=True)
        (dev / 'of_node/compatible').write_bytes(b'qcom,sm6115-adsp-pas\0')
        (edge / 'label').write_bytes(b'lpass\0')
        (edge / 'qcom,remote-pid').write_bytes(b'\0\0\0\2')
        remote = self.state.parent.parent / 'remoteproc93'
        remote.mkdir()
        (remote / 'device').symlink_to(dev)
        (remote / 'name').write_text('adsp\n')
        (remote / 'state').write_text('running\n')
        transport = dev / 'a400000.remoteproc:glink-edge'
        transport.mkdir()
        (transport / 'of_node').symlink_to(edge)
        ipcrtr = transport / 'a400000.remoteproc.IPCRTR.-1.-1'
        ipcrtr.mkdir()
        (ipcrtr / 'driver').symlink_to(self.driver_link.parent)
        (self.rpmsg / ipcrtr.name).symlink_to(ipcrtr)
        (self.driver_link.parent / ipcrtr.name).symlink_to(ipcrtr)
        lookup = self.bin / 'qrtr-lookup-fixture'
        for node in (2, 42):
            with self.subTest(adsp_node=node):
                # SNS may appear after other ADSP services in the listing.
                lookup.write_text('#!' + shutil.which('sh') + "\ncat <<'END'\n" +
                                  '  Service Version Instance Node  Port\n14 1 0 7 14\n' +
                                  f'43 2 0 {node} 100\n400 1 0 {node} 101\nEND\n' +
                                  f'if grep -qx running {self.state}; then echo "64 1 0 0 202"; fi\n')
                self.state.write_text('offline\n'); self.set_ipcrtr(False)
                r = self.function_probe('topology before\n')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.state.write_text('running\n'); self.set_ipcrtr(True)
                r = self.function_probe('topology after\n')
                self.assertEqual(r.returncode, 0, r.stderr)
        (self.d / 'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
        r, proc, functions = self.release_probe(self.RELEASE_BODY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('RESULT released', r.stdout)
        self.assertEqual((self.d / 'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:1\n')
        # An ADSP link alone cannot count as the modem's freshly probed link.
        self.set_ipcrtr(False)
        r, _, _ = self.release_probe('rc=0; release_check || rc=$?; echo "CHECK $rc"\n', functions, proc)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('CHECK 2', r.stdout)
        self.assertEqual(self.function_probe('topology after\n').returncode, 2)
        self.set_ipcrtr(True)
        listing = lookup.read_text()
        with lookup.open('a') as out:
            out.write("echo '1 1 0 33 200'\n")
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        lookup.write_text(listing)
        (remote / 'name').write_text('cdsp\n')
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)

    def test_static_topology_refuses_extra_or_unbound_transports_and_wrong_ns_nodes(self):
        self.state.write_text('running\n');self.set_ipcrtr(True)
        for module in ('qrtr_tun','qrtr_mhi'):
            path=self.root / 'sys/module' / module;path.mkdir(parents=True)
            self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
            path.rmdir()
        self.driver_link.unlink()
        self.driver_link.symlink_to(self.root)  # driver census disagrees with device census
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        self.driver_link.unlink();self.driver_link.symlink_to(self.ipcrtr)
        extra=self.rpmsg / 'adsp.IPCRTR.-1.-1';extra.symlink_to(self.root)
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        extra.unlink()
        driver=self.ipcrtr / 'driver';driver.unlink()
        self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        driver.symlink_to(self.driver_link.parent)
        lookup=self.bin / 'qrtr-lookup-fixture';original=lookup.read_text()
        for rows in ('  Service Version Instance Node  Port\n1 1 0 2 99\n',
                     '  Service Version Instance Node  Port\n',
                     'bad header\n1 1 0 0 99\n'):
            lookup.write_text('#!'+shutil.which('sh')+'\ncat <<\'END\'\n'+rows+'END\n')
            self.assertNotEqual(self.function_probe('topology after\n').returncode, 0)
        lookup.write_text(original)
        self.assertEqual(self.function_probe('topology after\n').returncode, 0)

    def release_probe(self, body, functions=None, proc=None, env=None, prefix='', wrap=None, timeout=10):
        """Run the watcher pieces of the production controller against the fixture.

        The probe plays the part of --start-guarded up to the start write: it
        prepares the fifo/pids, then `body` runs the release step. All functions
        are the production ones."""
        if functions is None:
            proc, functions = self.fresh_fixture()
        for name in ('start-attempted', 'start-write-pending', 'start-write-returned'):
            (self.d / name).touch()
        probe = self.root / 'release-probe.sh'
        probe.write_text(functions + "\n" + prefix + """
printf '%s\\n' $$ > "$D/watchdog.pid"; printf '%s\\n' $$ > "$D/guardian.pid"
release_prepare || exit 31
rel_end=$(( $(cut -d. -f1 UPTIME) + 10 ))
""".replace('UPTIME', str(self.root / 'uptime')) + body)
        argv = [shutil.which('sh'), str(probe)]
        if wrap: argv = wrap + argv
        return subprocess.run(argv, env=self.env | (env or {}), capture_output=True, text=True,
                              timeout=timeout * TIMEOUT_SCALE), proc, functions

    RELEASE_BODY = """
echo go >&9
release_watch | dd of="$D/rmtfs.admission" bs=1 seek=11 count=1 conv=notrunc 2>/dev/null
read -r result < "$D/release-watch.status" || result=missing
echo "RESULT $result"
"""

    def test_release_minimal_check_releases_only_with_every_condition(self):
        self.state.write_text('running\n');self.set_ipcrtr(True)
        r, proc, functions = self.release_probe(self.RELEASE_BODY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('RESULT released', r.stdout)
        self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:1\n')
        self.assertIn('release-begin', (self.d/'release-stamps').read_text())
        self.assertIn('release-written', (self.d/'release-stamps').read_text())
        def refuse(name, mutate, restore):
            with self.subTest(refusal=name):
                (self.d/'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
                (self.d/'release-stamps').unlink(missing_ok=True)
                mutate()
                try:
                    r, _, _ = self.release_probe(self.RELEASE_BODY, functions, proc)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn('RESULT refused', r.stdout, name)
                    self.assertEqual((self.d/'rmtfs.admission').read_bytes()[:11] + b'?', b'A9RMTFS1:0:?', name)
                    if name != 'admission-already-released':
                        self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n', name)
                finally:
                    restore()
        def write(path, data):
            old = path.read_bytes() if path.exists() else None
            def mutate(): path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            def restore():
                if old is None: path.unlink(missing_ok=True)
                else: path.write_bytes(old)
                self.set_ipcrtr(True)  # the sysfs emulation drops links while 'offline'
            return mutate, restore
        for name, path, data in (
                ('state', self.state, b'crashed\n'),
                ('recovery', self.state.parent/'recovery', b'enabled\n'),
                ('rmtfs-exited', self.d/'rmtfs.exit', b'1\n'),
                ('rmtfs-recycled-start', proc/'201/stat', b'201 (rmtfs) S 101 ' + b'0 ' * 17 + b'999\n'),
                ('rmtfs-reparented', proc/'201/stat', b'201 (rmtfs) S 999 ' + b'0 ' * 17 + b'2345\n'),
                ('rmtfs-zombie', proc/'201/stat', b'201 (rmtfs) Z 101 ' + b'0 ' * 17 + b'2345\n'),
                ('fault', self.d/'fault', b'x\n'), ('abort', self.d/'abort', b'x\n'),
                ('stop-requested', self.d/'stop-requested', b'x\n'),
                ('admission-already-released', self.d/'rmtfs.admission', b'A9RMTFS1:0:1\n')):
            m, rs = write(path, data); refuse(name, m, rs)
        for name in ('qrtr_tun', 'qrtr_mhi'):
            d = self.root/'sys/module'/name
            refuse(name, lambda d=d: d.mkdir(parents=True), lambda d=d: d.rmdir())
        other = self.root/'sys/class/remoteproc/remoteproc9'
        def add_other(): other.mkdir(); (other/'state').write_text('running\n')
        def del_other(): (other/'state').unlink(); other.rmdir()
        refuse('other-remoteproc-running', add_other, del_other)
        extra = self.rpmsg/'adsp.IPCRTR.-1.-1'
        refuse('second-ipcrtr', lambda: extra.symlink_to(self.ipcrtr), extra.unlink)
        stray = self.driver_link.parent/'stray'
        refuse('unbound-driver-entry', lambda: stray.symlink_to(self.root), stray.unlink)
        # Admission file replaced by a different inode: rmtfs would hold the old one.
        fd16 = proc/'201/fd/16'; target = fd16.readlink()
        other_gate = self.root/'other-gate'; other_gate.write_bytes(b'A9RMTFS1:0:0\n')
        refuse('different-gate-inode', lambda: (fd16.unlink(), fd16.symlink_to(other_gate)),
               lambda: (fd16.unlink(), fd16.symlink_to(target)))
        # Deadline exhausted (rel_end is never in the future).
        with self.subTest(refusal='deadline'):
            (self.d/'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
            r, _, _ = self.release_probe(self.RELEASE_BODY.replace('echo go', 'rel_end=0; echo go'), functions, proc)
            self.assertIn('RESULT refused', r.stdout)
            self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')

    def test_release_waits_for_unprobed_ipcrtr_then_releases_and_stopped_watcher_never_does(self):
        self.state.write_text('running\n');self.set_ipcrtr(False)
        proc, functions = self.fresh_fixture()
        timer = threading.Timer(0.3, lambda: self.set_ipcrtr(True)); timer.start()
        try:
            r, _, _ = self.release_probe(self.RELEASE_BODY, functions, proc)
        finally: timer.cancel()
        self.assertIn('RESULT released', r.stdout, r.stderr)
        self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:1\n')
        # A transient (non-running) state is retried only until the shared
        # deadline. The back-off sleep advances the fixture clock past it.
        (self.d/'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
        self.state.write_text('start\n');self.allow_start = False
        hook = 'sleep() { echo "11.0 0.0" > "' + str(self.root/'uptime') + '"; }\n'
        try:
            r, _, _ = self.release_probe(self.RELEASE_BODY, functions, proc, prefix=hook)
        finally: (self.root/'uptime').write_text('0.0 0.0\n')
        self.assertIn('RESULT refused-minimal-check', r.stdout, r.stderr)
        self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')
        self.state.write_text('running\n')
        # The EXIT-trap stop token must never release.
        (self.d/'rmtfs.admission').write_bytes(b'A9RMTFS1:0:0\n')
        r, _, _ = self.release_probe(self.RELEASE_BODY.replace('echo go', 'echo stop'), functions, proc)
        self.assertIn('RESULT refused-stopped', r.stdout)
        self.assertEqual((self.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')

    def test_release_path_forks_and_execs_nothing_between_wakeup_and_gate_byte(self):
        if not shutil.which('strace'): self.skipTest('host strace required')
        self.state.write_text('running\n');self.set_ipcrtr(True)
        body = """
echo go >&9
exec 7>"$D/release-out"
echo MARKA-BEGIN >&2
release_watch >&7
echo MARKB-END >&2
read -r result < "$D/release-watch.status"; echo "RESULT $result"
"""
        log = self.root / 'strace.log'
        r, _, _ = self.release_probe(body, wrap=['strace', '-f', '-qq', '-o', str(log),
                                      '-e', 'trace=execve,clone,clone3,fork,vfork,write'])
        if 'ptrace' in r.stderr and 'not permitted' in r.stderr: self.skipTest('ptrace unavailable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('RESULT released', r.stdout)
        self.assertEqual((self.d/'release-out').read_bytes(), b'1')
        lines = log.read_text().splitlines()
        begin = next(i for i, l in enumerate(lines) if 'MARKA-BEGIN' in l)
        end = next(i for i, l in enumerate(lines) if 'MARKB-END' in l)
        spawned = [l for l in lines[begin:end] if any(f'{n}(' in l for n in ('execve', 'clone', 'clone3', 'fork', 'vfork'))]
        self.assertEqual(spawned, [], 'release critical path must be fork-free')
        # The one byte is the only data written to the gate pipe.
        self.assertEqual(sum('write(1, "1", 1)' in l for l in lines[begin:end]), 1)

    def test_release_functions_use_only_mksh_builtins_statically(self):
        # mksh has no printf builtin: printf, cat, stat, ... all fork+exec on the phone.
        import re
        text = (HERE / 'coordinator-target.sh').read_text()
        for name in ('release_mark', 'release_check', 'release_watch', 'read_process_record'):
            body = re.search(r'^' + name + r'\(\) \{\n.*?^\}\n', text, re.S | re.M).group(0)
            code = '\n'.join(l.split(' #', 1)[0] for l in body.splitlines() if not l.strip().startswith('#'))
            code = code.replace('||', ' ').replace('2>/dev/null', ' ')
            self.assertNotRegex(code, r'\$\((?!\()|`|\s\|\s', name + ' forks (substitution/pipe)')
            self.assertNotRegex(code, r'(?<![\w/.$-])(printf|cat|cut|sed|awk|grep|tr|stat|readlink|dd|od|ls|touch|rm|mkfifo|sha256sum|id|uname|timeout|head|tail)(?![\w-])',
                                name + ' uses a non-builtin command')
            if name != 'release_watch':
                self.assertNotRegex(code, r'(?<![\w-])sleep(?![\w-])', name + ' sleeps')

    def test_release_without_path_commands_still_decides_correctly(self):
        # PATH-poison: every external command would be "not found". Builtins only.
        self.state.write_text('running\n');self.set_ipcrtr(True)
        body = """
echo go >&9
PATH=/nonexistent
release_watch > "$D/release-out"
read -r result < "$D/release-watch.status"; echo "RESULT $result"
"""
        r, _, _ = self.release_probe(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('RESULT released', r.stdout)
        self.assertNotIn('not found', r.stderr)

    def test_post_release_census_disagreement_revokes_gate_faults_and_stops(self):
        for census in ('ok', 'disagree'):
            with self.subTest(census=census):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    hook = '' if census == 'ok' else '\nverified_topology() { :; }\ntopology() { [ "$1" != after ] || return 1; }\n'
                    f.start_fixture(hook)
                    r = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
                    stamps = [l.split()[1] for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')]
                    for needed in ('release-begin', 'release-written', 'rmtfs-admitted', 'census-begin'):
                        self.assertIn(needed, stamps, r.stdout + r.stderr)
                    # Release precedes the census in the stamped order.
                    self.assertLess(stamps.index('rmtfs-admitted'), stamps.index('census-begin'))
                    if census == 'ok':
                        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                        self.assertEqual((f.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:1\n')
                    else:
                        self.assertNotEqual(r.returncode, 0)
                        self.assertIn('post-release', r.stderr)
                        self.assertEqual((f.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')  # revoked
                        self.assertIn('census disagrees', (f.d/'fault').read_text())
                finally: f.doCleanups()

    def test_run8_measured_proof_cost_is_after_the_gate_not_before_it(self):
        run8 = json.loads((Path(__file__).with_name('phone-budget-timings.json')).read_text())['run8_release']
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0, topology=run8['old_post_start_proof_seconds'])
            r = f.timed_run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')}
            self.assertLess(marks['rmtfs-admitted'] - marks['start-returned'], run8['critical_path_limit_seconds'])
            self.assertLess(marks['release-written'] - marks['release-begin'], run8['critical_path_limit_seconds'])
            self.assertAlmostEqual(marks['census-end'] - marks['census-begin'], run8['old_post_start_proof_seconds'], places=2)
            self.assertLessEqual(marks['rmtfs-admitted'], marks['census-begin'])
        finally: f.doCleanups()

    def test_audit_of_a_helper_that_exited_on_its_own_fails_and_names_the_role(self):
        proc, functions = self.fresh_fixture()
        ok = self.function_probe('audit && echo "AUDIT-OK:[$audit_failed_role]"\n', functions)
        self.assertEqual(ok.returncode, 0, ok.stderr); self.assertIn('AUDIT-OK:[]', ok.stdout)
        (self.d/'rmtfs.exit').write_text('1\n')
        bad = self.function_probe('audit || { echo "AUDIT-FAILED:$audit_failed_role"; exit 0; }\nexit 5\n', functions)
        self.assertEqual(bad.returncode, 0, bad.stderr); self.assertIn('AUDIT-FAILED:rmtfs', bad.stdout)

    def test_census_after_modem_crash_is_labelled_a_consequence_not_a_disagreement(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            hook = ('\nverified_topology() { :; }\ntopology() { [ "$1" != after ] || { echo crashed > "' +
                    str(f.state) + '"; return 1; }; }\n')
            f.start_fixture(hook)
            r = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
            self.assertNotEqual(r.returncode, 0)
            fault = (f.d/'fault').read_text()
            self.assertNotIn('disagrees', fault + r.stderr)
            self.assertIn('consequence of an earlier', fault)
            self.assertEqual((f.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')  # still revoked
        finally: f.doCleanups()

    def teardown_files(self):
        for name in ('rmtfs', 'diag-router', 'tqftpserv'):
            (self.d / (name + '.exit')).write_text('0\n')
            (self.d / (name + '.log')).write_text('A9_WIFI_READY ' + name + '\nA9_WIFI_TEARDOWN ' + name + ' reason=bye\n')
        (self.d / 'stop-requested').write_text('requested\n')
        (self.d / 'teardown-signalled').write_text('rmtfs\ndiag-router\ntqftpserv\n')

    # ---- run 12: evidence survival, single stop writer, WLAN observation ----
    def test_every_fallback_call_names_its_site(self):
        import re
        text = (HERE / 'coordinator-target.sh').read_text()
        calls = [l for l in text.splitlines() if re.search(r'\bfallback\b', l) and not l.lstrip().startswith('#')
                 and not l.startswith('fallback()') and 'fb_' not in l and 'a9wifi-fallback' not in l
                 and 'HARD STOP' not in l and 'label=fallback' not in l and 'fallback-' not in l]
        self.assertGreater(len(calls), 25)
        bare = [l for l in calls if not re.search(r'\bfallback [a-z_]+-[0-9]+\b', l)]
        self.assertEqual(bare, [])
        labels = re.findall(r'\bfallback ([a-z_]+-[0-9]+)\b', text)
        self.assertEqual(len(labels), len(set(labels)), 'labels must be unique')

    def test_fallback_records_site_state_and_evidence_before_requesting_reset(self):
        proc, functions = self.fresh_fixture()
        (self.d / 'fault').write_text('companion-reported-fault\n')
        (self.d / 'tqftpserv.exit').write_text('70\n')
        (self.d / 'tqftpserv.log').write_text('A9_WIFI_READY tqftpserv\nA9_WIFI_FAULT tqftpserv request-error\n')
        self.state.write_text('running\n')
        r = self.function_probe('sleep() { :; }\nfallback test_site-7\n', functions)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('a9wifi-fallback site=test_site-7 state=running uptime=', r.stdout)
        self.assertIn('A9_WIFI_EVIDENCE begin label=fallback-test_site-7', r.stdout)
        self.assertIn('A9_WIFI_FAULT tqftpserv request-error', r.stdout)
        self.assertIn('exit tqftpserv=70', r.stdout)
        fault = (self.d / 'fault').read_text()
        self.assertLess(fault.index('a9wifi-fallback site=test_site-7'), fault.index('HARD STOP'))
        kmsg = (self.root / 'kmsg').read_text()
        self.assertIn('a9wifi-evidence a9wifi-fallback site=test_site-7 state=running', kmsg)
        self.assertIn('tqftpserv: A9_WIFI_FAULT tqftpserv request-error', kmsg)
        # The reset is only requested after the evidence reached stdout and kmsg.
        self.assertIn('setprop sys.powerctl reboot,bootloader', self.commandlog.read_text())

    def test_stream_evidence_is_bounded_and_carries_fault_logs_and_hashes(self):
        proc, functions = self.fresh_fixture()
        (self.d / 'fault').write_text('companion-or-controller-fault\n')
        (self.d / 'diag-router.log').write_text(''.join(f'line {i}\n' for i in range(500)) + 'A9_WIFI_DIAG_NOTE dropped reason=x\n')
        (self.d / 'rmtfs.log').write_text('A9_WIFI_RMTFS_STORE_SHA256 when=exit modemst1=' + 'a' * 64 + '\n')
        r = self.function_probe('stream_evidence unit\n', functions)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(len(r.stdout.splitlines()), 200)
        self.assertIn('line 499', r.stdout); self.assertNotIn('line 100\n', r.stdout)
        self.assertIn('A9_WIFI_RMTFS_STORE_SHA256 when=exit modemst1=' + 'a' * 64, r.stdout)
        self.assertIn('A9_WIFI_EVIDENCE end label=unit', r.stdout)
        self.assertIn('diag-router: A9_WIFI_DIAG_NOTE dropped reason=x', (self.root / 'kmsg').read_text())

    def test_watchdog_and_die_stream_evidence_before_the_stop(self):
        text = (HERE / 'coordinator-target.sh').read_text()
        a = text.index('stream_evidence "watchdog-$why"'); b = text.index('stop_modem', a)
        self.assertLess(a, b)
        self.assertIn('stream_evidence "die"', text)
        self.assertIn('stream_evidence stop || :', text)

    def test_stop_state_has_one_writer_and_a_failed_second_write_is_not_an_error(self):
        text = self.script.read_text()
        wr = 'echo stop > "$m/state" || [ "$(cat "$m/state")" = offline ] || exit 1'
        self.assertEqual(text.count(wr), 1)
        self.assertIn(wr, text)
        slow = text.replace(wr, '{ echo w >> "$D/stop-writes"; sleep 1; echo stop > "$m/state"; } || [ "$(cat "$m/state")" = offline ] || exit 1')
        self.script.write_text(slow)
        self.allow_start = False; self.state.write_text('running\n')
        procs = [subprocess.Popen([shutil.which('sh'), str(self.script), '--stop-state'], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        outs = [p.communicate(timeout=20 * TIMEOUT_SCALE) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0], outs)
        self.assertEqual((self.d / 'stop-writes').read_text().split(), ['w'])
        self.assertEqual(self.state.read_text().strip(), 'offline')
        # A write that errors because the modem already went offline is tolerated...
        shutil.rmtree(self.d / 'stop-writer', ignore_errors=True); self.state.write_text('running\n')
        self.script.write_text(text.replace(wr, '{ echo stop > "$m/state"; sleep 1; sleep 1; false; } || [ "$(cat "$m/state")" = offline ] || exit 1'))
        r = subprocess.run([shutil.which('sh'), str(self.script), '--stop-state'], env=self.env, capture_output=True, text=True, timeout=20 * TIMEOUT_SCALE)
        self.assertEqual(r.returncode, 0, r.stderr)
        # ...but only then: a failed write with the modem still running is an error.
        shutil.rmtree(self.d / 'stop-writer', ignore_errors=True); self.state.write_text('running\n'); self.stuck = True
        self.script.write_text(text.replace(wr, 'false || [ "$(cat "$m/state")" = offline ] || exit 1'))
        r = subprocess.run([shutil.which('sh'), str(self.script), '--stop-state'], env=self.env, capture_output=True, text=True, timeout=20 * TIMEOUT_SCALE)
        self.assertEqual(r.returncode, 1)

    def test_stale_stop_writer_lock_is_taken_over_not_waited_on(self):
        self.allow_start = False; self.state.write_text('running\n')
        lock = self.d / 'stop-writer'; lock.mkdir(); (lock / 'pid').write_text('999999\n')   # owner long gone
        r = subprocess.run([shutil.which('sh'), str(self.script), '--stop-state'], env=self.env, capture_output=True, text=True, timeout=20 * TIMEOUT_SCALE)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(self.state.read_text().strip(), 'offline')
        # A lock with a live owner is respected: the loser only waits for offline.
        shutil.rmtree(lock); lock.mkdir(); (lock / 'pid').write_text(str(os.getpid()) + '\n')
        self.state.write_text('running\n'); self.stuck = True
        p = subprocess.Popen([shutil.which('sh'), str(self.script), '--stop-state'], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(3 * TIMEOUT_SCALE); self.assertIsNone(p.poll())          # still waiting, never wrote
        self.assertEqual(self.state.read_text().strip(), 'running')
        self.stuck = False; self.state.write_text('stop\n')       # the (other) owner's stop lands
        p.communicate(timeout=20 * TIMEOUT_SCALE)
        self.assertEqual(p.returncode, 0)

    def test_slow_wlan_up_modem_stop_of_seven_seconds_completes_without_fallback(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0, stop=7)
            r = f.timed_run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('a9wifi-fallback', r.stdout + r.stderr)
            marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')}
            self.assertGreaterEqual(marks['modem-offline'] - marks['stop-request'], 7)
            self.assertTrue((f.d / 'complete').exists())
        finally: f.doCleanups()

    def wlan_probe(self, body, ip_script='', iw_script=None, clock_jump=False, net=True, phy=True):
        if not hasattr(self, '_wlan_fx'): self._wlan_fx = self.fresh_fixture()
        proc, functions = self._wlan_fx
        net_dir, phy_dir = self.root / 'net', self.root / 'ieee80211'
        if net: (net_dir / 'wlan0').mkdir(parents=True, exist_ok=True)
        if phy: (phy_dir / 'phy0').mkdir(parents=True, exist_ok=True)
        for name, val in (('operstate', 'down'), ('type', '1'), ('flags', '0x1002'), ('carrier', '0')):
            if net: (net_dir / 'wlan0' / name).write_text(val + '\n')
        iw = self.script.parent / 'iw'
        iw.write_text('#!' + shutil.which('sh') + '\n' + (iw_script or r"""
case "$1 $2 $3 $4" in
"dev   ") echo 'phy#0'; echo '	Interface wlan0'; echo '		addr 12:34:56:78:9a:bc'; echo '		type managed';;
"phy   ") echo 'Wiphy phy0'; echo '	max # scan SSIDs: 9';;
"dev wlan0 scan passive") printf 'BSS aa:bb:cc:dd:ee:01(on wlan0)\n\tfreq: 2412\n\tSSID: NEIGHBOUR\nBSS aa:bb:cc:dd:ee:02(on wlan0)\n\tfreq: 5180\n';;
esac
"""))
        iw.chmod(0o700)
        # ip is an executable (the stage runs it under timeout), not a shell function.
        ipbin = self.bin / 'ip'
        ipbin.write_text('#!' + shutil.which('sh') + '\nD="' + str(self.d) + '"\necho "ip $*" >> "$D/ip-log"\n' + (ip_script or ':') + '\n')
        ipbin.chmod(0o700)
        hooks = ('dmesg() { echo "[ 1.0] ath10k_snoc: firmware ready mac 12:34:56:78:9a:bc"; echo unrelated; }\n'
                 'monitors() { [ ! -e "$D/fault" ]; }\nsleep() { if [ -n "$CLOCK_JUMP" ]; then echo "99.0 0.0" > "$FIXTURE_UPTIME"; fi; }\n')
        text = functions.replace('/sys/class/ieee80211', str(phy_dir)).replace('/sys/class/net', str(net_dir))
        env = {'CLOCK_JUMP': '1' if clock_jump else '', 'FIXTURE_UPTIME': str(self.root / 'uptime')}
        r = self.function_probe('m=' + str(self.state.parent) + '\n' + hooks + body, text) if False else None
        probe = self.root / 'wlan-probe.sh'
        probe.write_text(text + '\n' + hooks + 'm=' + str(self.state.parent) + '\n' + body)
        return subprocess.run([shutil.which('sh'), str(probe)], env=self.env | env, capture_output=True, text=True, timeout=30 * TIMEOUT_SCALE)

    def test_wlan_observation_lists_scans_passively_sanitises_and_links_down(self):
        self.state.write_text('running\n')
        r = self.wlan_probe('wlan_survey\ncat "$D/wlan-observe-result"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn('A9_WIFI_WLAN step=sysfs', out); self.assertIn('ieee80211: phy0', out); self.assertIn('net: wlan0', out)
        self.assertIn('A9_WIFI_WLAN step=iw-dev', out); self.assertIn('Interface wlan0', out)
        self.assertIn('A9_WIFI_WLAN step=scan-passive', out); self.assertIn('bss_count=2', out)
        self.assertIn('freq: 2412', out)
        self.assertNotIn('12:34:56:78:9a:bc', out); self.assertNotIn('NEIGHBOUR', out)   # MAC and SSIDs never printed
        self.assertIn('xx:xx:xx:xx:xx:xx', out); self.assertNotIn('unrelated', out)
        self.assertEqual((self.d / 'ip-log').read_text().splitlines(), ['ip link set wlan0 up', 'ip link set wlan0 down'])
        self.assertTrue((self.d / 'wlan-down-done').exists())
        self.assertEqual((self.d / 'wlan-observe-result').read_text().strip(), '')

    def test_wlan_observation_failures_are_recorded_and_the_link_always_goes_down(self):
        self.state.write_text('running\n')
        cases = {
            'scan-fails': dict(iw_script='case "$1 $2 $3 $4" in "dev   ") echo ok;; "phy   ") echo ok;; "dev wlan0 scan passive") exit 3;; esac', want='passive scan rc=3', down=True),
            'scan-times-out': dict(iw_script='case "$1 $2 $3 $4" in "dev   ") echo ok;; "phy   ") echo ok;; "dev wlan0 scan passive") exit 124;; esac', want='rc=124', down=True),
            'ip-up-fails': dict(ip_script='[ "$4" != up ] || exit 1', want='ip link up', down=True),
            'iw-dev-fails': dict(iw_script='exit 1', want='iw dev', down=False),
            'no-wlan0': dict(net=False, clock_jump=True, want='phy/wlan0 timeout', down=False),
            'no-phy': dict(phy=False, clock_jump=True, want='phy/wlan0 timeout', down=False),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                for leftover in ('ip-log', 'wlan-down-done', 'wlan-up-requested', 'scan-passive.txt'):
                    (self.d / leftover).unlink(missing_ok=True)
                shutil.rmtree(self.root / 'net', ignore_errors=True); shutil.rmtree(self.root / 'ieee80211', ignore_errors=True)
                (self.root / 'uptime').write_text('0.0 0.0\n')
                want, down = kw.pop('want'), kw.pop('down')
                r = self.wlan_probe('wlan_survey\ncat "$D/wlan-observe-result"\n', **kw)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(want, (self.d / 'wlan-observe-result').read_text())
                log = (self.d / 'ip-log').read_text().splitlines() if (self.d / 'ip-log').exists() else []
                if down: self.assertEqual(log[-1], 'ip link set wlan0 down', log)
                else: self.assertNotIn('ip link set wlan0 up', log)
                if name in ('no-wlan0', 'no-phy'):
                    self.assertNotIn('A9_WIFI_WLAN step=scan-passive', r.stdout)  # never scans an unproven interface

    def test_wlan_link_down_by_the_stop_path_if_the_stage_was_killed_and_output_is_bounded(self):
        self.state.write_text('running\n')
        (self.d / 'wlan-up-requested').write_text('requested\n')
        r = self.wlan_probe('wlan_link_down\nwlan_link_down\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.d / 'ip-log').read_text().splitlines(), ['ip link set wlan0 down'])  # idempotent
        (self.d / 'ip-log').unlink(); (self.d / 'wlan-down-done').unlink()
        (self.d / 'wlan-up-requested').unlink()
        r = self.wlan_probe('wlan_link_down\n'); self.assertFalse((self.d / 'ip-log').exists())  # never brought up: no call
        big = 'case "$1 $2 $3 $4" in "dev   ") seq 1 5000;; "phy   ") seq 1 5000;; "dev wlan0 scan passive") :;; esac'
        r = self.wlan_probe('wlan_survey\n', iw_script=big)
        self.assertLess(len(r.stdout.splitlines()), 1200)

    def test_observe_wlan_is_exclusive_with_android_and_reviewed_scan(self):
        for extra in (['--android'], ['--scan-reviewed', '1' * 64]):
            r = self.run_stage('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64, '--observe-wlan', *extra)
            self.assertNotEqual(r.returncode, 0)

    def test_trial12_replay_observe_wlan_run_completes_with_scan_and_link_down(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0)
            net, phy = f.root / 'net', f.root / 'ieee80211'
            (phy / 'phy0').mkdir(parents=True); (net / 'wlan0').mkdir(parents=True, exist_ok=True)
            iw = f.script.parent / 'iw'
            iw.write_text('#!' + shutil.which('sh') + '\ncase "$1 $2 $3 $4" in "dev   ") echo wlan0;; "dev wlan0 scan passive") printf "BSS aa:bb:cc:dd:ee:01(on wlan0)\\n\\tfreq: 2412\\n";; esac\n')
            iw.chmod(0o700)
            (f.bin / 'ip').write_text('#!' + shutil.which('sh') + '\necho "$*" >> "' + str(f.d / 'ip-log') + '"\n'); (f.bin / 'ip').chmod(0o700)
            text = f.script.read_text().replace('/sys/class/ieee80211', str(phy)).replace('/sys/class/net/wlan0', str(net / 'wlan0'))
            f.script.write_text(text)
            r = f.run_stage('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64, '--observe-wlan', timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr + ((f.d/'fault').read_text() if (f.d/'fault').exists() else ''))
            self.assertIn('OBSERVED:', r.stdout); self.assertIn('bss_count=1', r.stdout)
            self.assertEqual((f.d / 'ip-log').read_text().splitlines()[-1], 'link set wlan0 down')
            self.assertTrue((f.d / 'complete').exists())
            self.assertFalse((f.d / 'fault').exists())
        finally: f.doCleanups()

    def test_expected_teardown_passes_the_audit_only_strictly_after_stop_request(self):
        _, functions = self.fresh_fixture(); self.teardown_files()
        probe = lambda body: self.function_probe(body, functions)
        # pd-mapper has no teardown mode; tqftpserv now does (run 14) but only with marker + signal.
        (self.d/'teardown-signalled').write_text('rmtfs\ndiag-router\n')
        r = probe('audit || echo "FAILED:$audit_failed_role"\n'); self.assertIn('FAILED:tqftpserv', r.stdout)
        (self.d/'teardown-signalled').write_text('rmtfs\ndiag-router\ntqftpserv\n')
        r = probe('audit && echo "AUDIT-OK"\nexpected_teardown rmtfs && expected_teardown diag-router && expected_teardown tqftpserv && echo TD-OK\n')
        self.assertIn('AUDIT-OK', r.stdout, r.stderr); self.assertIn('TD-OK', r.stdout)
        cases = {
            'fault-before-any-stop-marker': lambda: (self.d/'stop-requested').unlink(),
            'nonzero-exit': lambda: (self.d/'rmtfs.exit').write_text('1\n'),
            'never-signalled': lambda: (self.d/'teardown-signalled').write_text('diag-router\n'),
            'no-marker': lambda: (self.d/'rmtfs.log').write_text('A9_WIFI_READY rmtfs\n'),
            'fault-line': lambda: (self.d/'rmtfs.log').write_text('A9_WIFI_READY rmtfs\nA9_WIFI_TEARDOWN rmtfs reason=bye\nA9_WIFI_FAULT rmtfs request-error\n'),
            'signalled-file-missing': lambda: (self.d/'teardown-signalled').unlink(),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.teardown_files()
                mutate()
                r = self.function_probe('audit || echo "FAILED:$audit_failed_role"\n', functions)
                self.assertIn('FAILED:rmtfs', r.stdout, name + r.stderr)

    def test_teardown_signal_goes_only_to_verified_rmtfs_and_diag_router_after_stop_request(self):
        proc, functions = self.fresh_fixture()
        hook = 'kill() { echo "$*" >> "$D/usr1-log"; }\n'
        r = self.function_probe(hook + 'teardown_signal\n', functions)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted((self.d/'usr1-log').read_text().splitlines()), ['-USR1 201', '-USR1 202', '-USR1 203'])
        self.assertEqual((self.d/'teardown-signalled').read_text().split(), ['rmtfs', 'diag-router', 'tqftpserv'])
        # A recycled/reparented child is never signalled.
        (self.d/'usr1-log').unlink(); (self.d/'teardown-signalled').unlink()
        (proc/'201/stat').write_text('201 (rmtfs) S 999 ' + '0 ' * 17 + '2345\n')
        r = self.function_probe(hook + 'teardown_signal\n', functions)
        self.assertEqual(sorted((self.d/'usr1-log').read_text().splitlines()), ['-USR1 202', '-USR1 203'])
        self.assertEqual((self.d/'teardown-signalled').read_text().split(), ['diag-router', 'tqftpserv'])

    def test_trial10_replay_teardown_after_stop_request_passes_audit_and_dumps_ramcopies(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0)
            text = f.script.read_text()
            old = '        teardown_signal\n'
            self.assertIn(old, text)
            # The daemons react to SIGUSR1 plus the modem going away: clean exit.
            sim = (old + '        for td in rmtfs diag-router tqftpserv; do echo 0 > "$D/$td.exit"; '
                   'echo "A9_WIFI_TEARDOWN $td reason=del-server" >> "$D/$td.log"; done\n')
            f.script.write_text(text.replace(old, sim, 1))
            r = f.timed_run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr + ((f.d/'fault').read_text() if (f.d/'fault').exists() else ''))
            self.assertFalse((f.d/'fault').exists())
            self.assertEqual(sorted((f.d/'usr1').read_text().split()), ['201', '202', '203'])
            marks = [l.split()[1] for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')]
            self.assertLess(marks.index('stop-request'), marks.index('teardown-signalled'))
            self.assertLess(marks.index('teardown-signalled'), marks.index('modem-offline'))
            lines = [l for l in r.stdout.splitlines() if l.startswith('A9_WIFI_RAMCOPY ')]
            self.assertEqual([l.split()[1] for l in lines], ['modemst1', 'modemst2', 'fsg', 'fsc'])
            for l in lines:
                self.assertRegex(l.split()[2], '^[0-9a-f]{64}$'); self.assertIn(l.split()[3], ('same', 'changed', 'unknown'))
        finally: f.doCleanups()

    def test_ramcopy_evidence_exists_and_compares_with_the_seeds_not_the_real_store(self):
        proc, functions = self.fresh_fixture()
        h = lambda c: c * 64
        (self.d/'rmtfs.log').write_text('A9_WIFI_READY rmtfs\nA9_WIFI_RMTFS_STORE_SHA256 when=teardown-exit modemst1=%s modemst2=%s fsg=%s fsc=%s\n' % (h('a'), h('b'), h('c'), h('d')))
        # Seeds (what rmtfs loaded) and the real stores' PRE hashes differ, as in run 13.
        seeds = self.root / 'assets.sha256'
        seeds.write_text(''.join(f'{n} 2097152 {d}\n' for n, d in (('rmtfs', h('f')), ('modemst1', h('9')), ('modemst2', h('b')), ('fsg', h('c')), ('fsc', h('d')))))
        (self.d/'stores/pre').mkdir(parents=True)
        for name, digest in (('modemst1', h('9')), ('modemst2', h('1')), ('fsg', h('c')), ('fsc', h('2'))):
            (self.d/'stores/pre'/(name+'.sha256')).write_text(digest + '\n')
        shutil.rmtree(proc/'201')  # the daemon has exited: no live fd to hash
        r = self.function_probe('ramcopy_evidence\n', functions.replace('/vendor/etc/a9-wifi/assets.sha256', str(seeds)))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = (self.d/'ramcopy-sha256.txt').read_text().splitlines()
        self.assertEqual([l.split()[1:4] for l in lines],
                         [['modemst1', h('a'), 'changed'], ['modemst2', h('b'), 'same'], ['fsg', h('c'), 'same'], ['fsc', h('d'), 'same']])
        # modemst2 equals its seed (label `same`) although the real store's PRE hash differs: no false alarm.
        self.assertIn('vs=seed pre-store=differs', lines[1]); self.assertIn('vs=seed pre-store=same', lines[2])
        self.assertTrue(all('source=rmtfs-log' in l for l in lines))
        self.assertIn('A9_WIFI_RAMCOPY modemst1', r.stdout)
        # Without a seed record the label is unknown, never a guess.
        r = self.function_probe('ramcopy_evidence\n', functions.replace('/vendor/etc/a9-wifi/assets.sha256', str(self.root / 'absent')))
        self.assertTrue(all(l.split()[3] == 'unknown' for l in (self.d/'ramcopy-sha256.txt').read_text().splitlines()))

    def gate_fixture(self, hooks='', observe=True, diag_log=None, handshake=False):
        f = Controller(methodName='runTest'); f.setUp()
        f.start_fixture('\nsleep() { echo "99.0 0.0" > "' + str(f.root / 'uptime') + '"; }\n' + hooks)
        log = diag_log if diag_log is not None else 'A9_WIFI_READY diag-router\nA9_WIFI_LOCAL_NODE 7\nA9_WIFI_DIAG command-peer-connected\nA9_WIFI_DIAG_NOTE dropped reason=control-handler\n'
        (f.d / 'diag-router.log').write_text(log + ('A9_WIFI_DIAG_HANDSHAKE features=0x41 mask_len=3\n' if handshake else ''))
        if observe: (f.d / 'observe-wlan-mode').write_text('observe-wlan\n')
        f.state.write_text('running\n')
        return f

    def test_relaxed_post_start_gate_is_observe_only_and_keeps_every_other_condition(self):
        cases = [
            ('observe, no handshake, diag alive, services ok', {}, 0, 'diag-handshake-absent'),
            ('plain check path still needs the handshake', dict(observe=False), 1, '30-second post-start DIAG control-handshake timeout'),
            ('diag-router exited', dict(setup=lambda f: (f.d / 'diag-router.exit').write_text('1\n')), 1, 'timeout'),
            ('diag-router logged a fault', dict(diag_log='A9_WIFI_READY diag-router\nA9_WIFI_FAULT diag-router frame-invalid\n'), 1, 'timeout'),
            ('services missing', dict(hooks='\nservices() { return 1; }\n'), 1, 'timeout'),
            ('monitor loss', dict(hooks='\nmonitors() { return 1; }\n'), 1, 'monitor/fault loss'),
            ('modem stopped', dict(setup=lambda f: f.state.write_text('offline\n')), 1, 'MPSS stopped'),
            ('handshake present: no note', dict(handshake=True, observe=False), 0, 'actual DIAG control handshake observed'),
        ]
        for name, kw, want_rc, want in cases:
            with self.subTest(case=name):
                setup = kw.pop('setup', None)
                f = self.gate_fixture(**kw)
                try:
                    if setup: setup(f)
                    r = f.run_stage('--post-start')
                    self.assertEqual(r.returncode, 0 if want_rc == 0 else r.returncode, r.stdout + r.stderr)
                    if want_rc: self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertIn(want, r.stdout + r.stderr)
                    if name.startswith('observe,'):
                        self.assertIn('A9_WIFI_DIAG_NOTE dropped reason=control-handler', r.stdout)
                        self.assertTrue((f.d / 'diag-handshake-absent').exists() and (f.d / 'services-ready').exists())
                    if name.startswith('handshake present'): self.assertNotIn('diag-handshake-absent', r.stdout)
                finally: f.doCleanups()

    def test_trial13_replay_observe_wlan_runs_without_a_diag_handshake(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0)
            # diag-router alive, no handshake (its feature reply failed once), no fault line.
            (f.d / 'diag-router.log').write_text('A9_WIFI_READY diag-router\nA9_WIFI_LOCAL_NODE 7\nA9_WIFI_DIAG command-peer-connected\n'
                                                 'A9_WIFI_DIAG_NOTE dropped reason=control-handler socket=0 step=features-emit emit=1 errno=32\n')
            net, phy = f.root / 'net', f.root / 'ieee80211'
            (phy / 'phy0').mkdir(parents=True); (net / 'wlan0').mkdir(parents=True, exist_ok=True)
            iw = f.script.parent / 'iw'
            iw.write_text('#!' + shutil.which('sh') + '\ncase "$1 $2 $3 $4" in "dev   ") echo wlan0;; "dev wlan0 scan passive") printf "BSS aa:bb:cc:dd:ee:01(on wlan0)\\n\\tfreq: 2412\\n";; esac\n'); iw.chmod(0o700)
            (f.bin / 'ip').write_text('#!' + shutil.which('sh') + '\necho "$*" >> "' + str(f.d / 'ip-log') + '"\n'); (f.bin / 'ip').chmod(0o700)
            f.script.write_text(f.script.read_text().replace('/sys/class/ieee80211', str(phy)).replace('/sys/class/net/wlan0', str(net / 'wlan0')))
            r = f.run_stage('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64, '--observe-wlan', timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr + ((f.d/'fault').read_text() if (f.d/'fault').exists() else ''))
            self.assertIn('A9_WIFI_NOTE diag-handshake-absent', r.stdout)
            self.assertIn('OBSERVED:', r.stdout); self.assertIn('bss_count=1', r.stdout)
            self.assertEqual((f.d / 'ip-log').read_text().splitlines()[-1], 'link set wlan0 down')
            self.assertTrue((f.d / 'complete').exists()); self.assertFalse((f.d / 'fault').exists())
        finally: f.doCleanups()

    def test_watchdog_marker_counts_as_a_stop_phase_but_never_as_stop_requested(self):
        _, functions = self.fresh_fixture(); self.teardown_files()
        (self.d / 'stop-requested').unlink(); (self.d / 'watchdog-stop').write_text('requested\n')
        r = self.function_probe('for r in rmtfs diag-router tqftpserv; do expected_teardown $r || echo "NO:$r"; done\naudit && echo AUDIT-OK\n', functions)
        self.assertNotIn('NO:', r.stdout, r.stdout); self.assertIn('AUDIT-OK', r.stdout)
        # Without either marker nothing is accepted, and the roles outside the three never are.
        (self.d / 'watchdog-stop').unlink()
        r = self.function_probe('expected_teardown rmtfs || echo NO\nexpected_teardown pd-mapper || echo NO2\n', functions)
        self.assertIn('NO\n', r.stdout); self.assertIn('NO2', r.stdout)
        # run_exit's interrupted-stop rule keys on stop-requested only.
        text = (HERE / 'coordinator-target.sh').read_text()
        self.assertIn('[ ! -e "$D/stop-requested" ] || fallback', text)
        a = text.index('watchdog_teardown || :'); self.assertLess(a, text.index('stop_modem', a))
        self.assertNotIn('echo requested > "$D/stop-requested"', text[text.index('watchdog_teardown() {'):text.index('first_fault() {')])

    def test_watchdog_teardown_writes_its_own_marker_and_signals_the_three_helpers(self):
        proc, functions = self.fresh_fixture()
        hook = 'kill() { echo "$*" >> "$D/usr1-log"; }\n'
        r = self.function_probe(hook + 'watchdog_teardown\n', functions)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.d / 'watchdog-stop').exists()); self.assertFalse((self.d / 'stop-requested').exists())
        self.assertEqual(sorted((self.d / 'usr1-log').read_text().splitlines()), ['-USR1 201', '-USR1 202', '-USR1 203'])
        # The role that already faulted and exited is NOT signalled and gets no credit.
        (self.d / 'usr1-log').unlink(); (self.d / 'teardown-signalled').unlink(); (self.d / 'watchdog-stop').unlink()
        (self.d / 'tqftpserv.exit').write_text('70\n')
        r = self.function_probe(hook + 'watchdog_teardown\n', functions)
        self.assertEqual(sorted((self.d / 'usr1-log').read_text().splitlines()), ['-USR1 201', '-USR1 203'])
        self.assertEqual(sorted((self.d / 'teardown-signalled').read_text().split()), ['diag-router', 'rmtfs'])

    def test_first_fault_names_the_earliest_faulting_role_not_the_teardown_noise(self):
        _, functions = self.fresh_fixture()
        for name, code, line in (('tqftpserv', '70', 'A9_WIFI_FAULT tqftpserv active-client-disappeared'),
                                 ('rmtfs', '1', 'A9_WIFI_FAULT rmtfs mpss-del-server')):
            (self.d / (name + '.exit')).write_text(code + '\n')
            (self.d / (name + '.log')).write_text('A9_WIFI_READY ' + name + '\n' + line + '\n')
            time.sleep(0.05)   # rmtfs exits later than tqftpserv
        (self.d / 'fault').write_text('companion-reported-fault\n')
        r = self.function_probe('first_fault\nfirst_fault\n', functions)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.splitlines() if l.startswith('A9_WIFI_FIRST_FAULT')]
        self.assertEqual(lines, ['A9_WIFI_FIRST_FAULT role=tqftpserv exit=70 reason=A9_WIFI_FAULT tqftpserv active-client-disappeared'] * 2)
        self.assertIn('first-fault', (self.root / 'kmsg').read_text().lower().replace('_', '-'))
        # stream_evidence prints it before the evidence blocks.
        r = self.function_probe('stream_evidence unit\n', functions)
        self.assertLess(r.stdout.index('A9_WIFI_FIRST_FAULT'), r.stdout.index('A9_WIFI_EVIDENCE begin'))
        # No faulting role and no fault file: nothing is invented.
        (self.d / 'first-fault').unlink(); (self.d / 'fault').unlink()
        for name in ('tqftpserv', 'rmtfs'): (self.d / (name + '.exit')).unlink()
        r = self.function_probe('first_fault\n', functions); self.assertNotIn('A9_WIFI_FIRST_FAULT', r.stdout)

    def test_trial14_replay_watchdog_stop_noise_is_teardown_only_tqftpserv_fails(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0)
            text = f.script.read_text()
            old = '        stamp stop-request\n'
            self.assertIn(old, text)
            # tqftpserv faulted first (client departed); the watchdog stopped the modem with its own
            # teardown phase, so rmtfs/diag-router exited cleanly afterwards; then the controller stops.
            sim = ('    --stop-body)\n        rc=0\n'
                   '        echo 70 > "$D/tqftpserv.exit"; echo "A9_WIFI_FAULT tqftpserv active-client-disappeared" >> "$D/tqftpserv.log"\n'
                   '        watchdog_teardown\n'
                   '        for td in rmtfs diag-router; do echo 0 > "$D/$td.exit"; echo "A9_WIFI_TEARDOWN $td reason=del-server" >> "$D/$td.log"; done\n')
            f.script.write_text(text.replace('    --stop-body)\n        rc=0\n', sim, 1))
            r = f.timed_run()
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)          # the primary fault still fails the trial
            self.assertNotIn('a9wifi-fallback', r.stdout + r.stderr)             # watchdog marker != stop-requested
            fault = (f.d / 'fault').read_text()
            self.assertIn('stop-audit-failed tqftpserv', fault)
            self.assertNotIn('stop-audit-failed rmtfs', fault); self.assertNotIn('stop-audit-failed diag-router', fault)
            self.assertIn('A9_WIFI_FIRST_FAULT role=tqftpserv', r.stdout)
            self.assertTrue((f.d / 'watchdog-stop').exists() and (f.d / 'complete').exists())
        finally: f.doCleanups()

    def test_trial10_replay_pre_stop_exit_still_fails_even_if_it_looks_like_teardown(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.timed_fixture(proof=0)
            text = f.script.read_text()
            old = '        stamp stop-request\n'
            self.assertIn(old, text)
            # rmtfs exits 0 with a teardown-looking marker, but BEFORE stop-request.
            f.script.write_text(text.replace('    --stop-body)\n        rc=0\n', '    --stop-body)\n        rc=0\n'
                '        echo 0 > "$D/rmtfs.exit"; echo "A9_WIFI_TEARDOWN rmtfs reason=bye" >> "$D/rmtfs.log"\n', 1))
            r = f.timed_run()
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('stop-audit-failed rmtfs', (f.d/'fault').read_text())
        finally: f.doCleanups()

    def test_start_refuses_when_rmtfs_died_before_release_and_never_opens_gate(self):
        f = Controller(methodName='runTest'); f.setUp()
        try:
            f.start_fixture()
            # rmtfs exits on its own right as MPSS comes up (the run-8 shape).
            text = f.script.read_text()
            old = "'echo start > \"$1\" && echo returned > \"$2/start-write-returned\"'"
            self.assertIn(old, text)
            f.script.write_text(text.replace(old, old[:-1] + " && echo 1 > \"$2/rmtfs.exit\"'"))
            r = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('refused', r.stderr)
            self.assertEqual((f.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')
        finally: f.doCleanups()

    def test_topology_census_after_release_consumes_shared_ten_second_startup_budget(self):
        # The gate opens at once; a slow full census still fails closed against
        # the same ten-second budget (run 8: 2.47 s proof, 0.5 s start).
        for topology_cost, good in ((0.5, True), (2, False)):
            with self.subTest(topology_cost=topology_cost):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0, startup=9, topology=topology_cost)
                    r = f.timed_run()
                    self.assertEqual(r.returncode == 0, good, r.stdout + r.stderr)
                    self.assertTrue((f.d / 'start-write-returned').exists())
                    # Released in BOTH cases: the check is the minimal one. A
                    # failed census revokes it again.
                    self.assertEqual((f.d / 'rmtfs.admission').read_bytes(),
                                     b'A9RMTFS1:0:1\n' if good else b'A9RMTFS1:0:0\n')
                    if not good: self.assertIn('startup budget', r.stderr)
                finally: f.doCleanups()

    def test_admission_descriptor_cannot_be_writable_missing_or_a_different_inode(self):
        for change in ('writable','missing','replace'):
            with self.subTest(change=change):
                f=Controller(methodName='runTest');f.setUp()
                try:
                    proc,functions=f.fresh_fixture();fd=proc/'201/fd/16'
                    if change=='writable': (proc/'201/fdinfo/16').write_text('flags:\t0100002\n')
                    elif change=='missing':fd.unlink()
                    else:fd.unlink();fd.write_bytes(b'A9RMTFS1:0:0\n')
                    r=f.function_probe('role=rmtfs\npid=201\ndescriptors\n',functions)
                    self.assertNotEqual(r.returncode,0)
                finally:f.doCleanups()

    def test_tools_mount_rejects_inherited_noexec_or_unsafe_mount(self):
        mounts = self.root / 'mounts'
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/proc/mounts', str(mounts))
        parent = 'tmpfs /mnt tmpfs rw,nosuid,nodev,noexec 0 0\n'
        for kind, options, good in (('tmpfs', 'rw,nosuid,nodev', True), ('tmpfs', 'rw,nosuid,nodev,noexec', False),
                                    ('tmpfs', 'rw,nodev', False), ('ext4', 'rw,nosuid,nodev', False)):
            with self.subTest(kind=kind, options=options):
                mounts.write_text(parent + f'a9-tools {self.script.parent} {kind} {options} 0 0\n')
                result = self.function_probe('tools_mount\n', functions)
                self.assertEqual(result.returncode == 0, good, result.stderr)
        mounts.write_text(parent)
        self.assertNotEqual(self.function_probe('tools_mount\n', functions).returncode, 0)

    def prepare_launch_fixture(self, observation):
        self.state.write_text('offline\n')
        shutil.rmtree(self.d)
        fw = self.root / 'sys/module/firmware_class/parameters/path'
        fw.parent.mkdir(parents=True, exist_ok=True)
        fw.write_text(str(self.root / 'mnt/a9-wifi/firmware') + '\n')
        firmware = self.root / 'mnt/a9-wifi/firmware'
        firmware.mkdir(exist_ok=True)
        for name in ('qcom', 'ath10k', 'regulatory.db', 'regulatory.db.p7s', 'hisense'):
            if not (firmware/name).is_symlink(): (firmware / name).symlink_to('/nonexistent-fixture')
        modules = self.root / 'modules.wifi'
        modules.write_text('')
        mounts = self.root / 'mounts'
        mounts.write_text('/dev/block/ram0 /data ext4 rw,nosuid,nodev 0 0\n')
        runcon = self.bin / 'runcon-fixture'
        runcon.write_text('#!' + shutil.which('sh') + '\nshift\nexec "$@"\n')
        runcon.chmod(0o700)
        tracer = self.script.parent / 'strace'
        tracer.write_text('#!' + shutil.which('sh') + '''
if [ "$1" = --help ]; then echo --kill-on-exit; exit; fi
for arg do role=$arg; done
if [ "$role" = "${FIXTURE_FAIL_ROLE:-}" ]; then exit 1; fi
echo "A9_WIFI_READY $role"
exec sleep 60
''')
        tracer.chmod(0o700)
        text = self.script.read_text()
        text = text.replace('/system/bin/runcon', str(runcon)).replace('/proc/mounts', str(mounts))
        text = text.replace('/vendor/lib/modules/modules.wifi', str(modules))
        text = text.replace('/vendor/bin/a9ml-module-load.sh', str(REPO/'scripts/mainline/android/vendor/bin/a9ml-module-load.sh'))
        text = text.replace('/vendor/bin/a9ml-wifi-firmware.sh', 'true')
        data_guard = self.root/'data-guard.sh'
        data_guard.write_text('a9ml_test_data() { grep -qx "/dev/block/ram0 /data ext4 rw,nosuid,nodev 0 0" '+str(mounts)+'; }\n')
        text = text.replace('/vendor/bin/a9ml-test-data.sh', str(data_guard))
        hooks = '''
tool() { :; }
tools_mount() { :; }
mount() { :; }
stat() { case "$*" in *'%u:%a:%h:%s'*) echo 0:600:1:13;; *'%u:%a'*) echo 0:700;; *'%t:%T'*) echo 1:0;; *'%F'*) echo 'block device';; *'%T'*) echo tmpfs;; *) exit 99;; esac; }
observe() { ''' + observation + '''; }
audit() { :; }
diag_lookup() { :; }
'''
        self.script.write_text(text.replace('case "${1:-}" in', hooks + 'case "${1:-}" in', 1))

    def assert_tracers_stopped(self):
        for path in self.d.glob('*.tracer'):
            pid = path.read_text().strip()
            stat = Path('/proc') / pid / 'stat'
            if stat.exists():
                self.assertEqual(stat.read_text().rsplit(') ', 1)[1].split()[0], 'Z', 'live tracer ' + pid)

    def test_prepare_stop_and_shell_error_cleanup_started_helpers(self):
        self.prepare_launch_fixture('die injected-prepare-stop')
        result = self.run_stage('prepare', 'x', 'x', 'x', timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('injected-prepare-stop', result.stderr)
        self.assertEqual(len(list(self.d.glob('*.tracer'))), 3)
        self.assert_tracers_stopped()
        self.assertTrue((self.d / 'prepare-failed').exists())
        self.assertFalse((self.d / 'start-attempted').exists())
        self.assertEqual(self.state.read_text().strip(), 'offline')

    def test_abort_before_watchdog_synchronously_stops_prepared_helpers(self):
        self.prepare_launch_fixture(':')
        result = self.run_stage('prepare', 'x', 'x', 'x', timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.d / 'watchdog-ready').exists())
        result = self.run_stage('abort')
        self.assertEqual(result.returncode, 0, result.stderr)
        time.sleep(0.05)
        self.assert_tracers_stopped()
        self.assertIn('ABORTED', result.stdout)

    def test_prepare_raw_shell_failure_cleans_up(self):
        self.prepare_launch_fixture('false')
        result = self.run_stage('prepare', 'x', 'x', 'x', timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.d / 'prepare-cleaned').exists(), result.stderr)
        self.assert_tracers_stopped()

    def test_partial_prepare_launch_failure_cleans_up(self):
        self.prepare_launch_fixture(':')
        self.env['FIXTURE_FAIL_ROLE'] = 'tqftpserv'
        result = self.run_stage('prepare', 'x', 'x', 'x', timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.d / 'prepare-cleaned').exists(), result.stderr)
        self.assert_tracers_stopped()

    def test_abort_during_prepare_joins_cleanup_before_returning(self):
        self.prepare_launch_fixture('echo waiting > "$D/fixture-waiting"; while :; do sleep 1; done')
        process = subprocess.Popen([shutil.which('sh'), str(self.script), 'prepare', 'x', 'x', 'x'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5 * TIMEOUT_SCALE
            while not (self.d / 'fixture-waiting').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.d / 'fixture-waiting').exists())
            result = self.run_stage('abort', timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            process.communicate(timeout=8 * TIMEOUT_SCALE)
            self.assertNotEqual(process.returncode, 0)
            self.assertTrue((self.d / 'prepare-cleaned').exists())
            self.assert_tracers_stopped()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)

    def test_abort_while_successful_prepare_is_exiting(self):
        self.prepare_launch_fixture(':')
        text = self.script.read_text().replace("        echo 'PREPARED:",
            '        echo waiting > "$D/fixture-waiting"\n        while :; do sleep 1; done\n        echo \'PREPARED:', 1)
        self.script.write_text(text)
        process = subprocess.Popen([shutil.which('sh'), str(self.script), 'prepare', 'x', 'x', 'x'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5 * TIMEOUT_SCALE
            while not (self.d / 'fixture-waiting').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.d / 'prepare-finished').exists())
            result = self.run_stage('abort', timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            process.communicate(timeout=8 * TIMEOUT_SCALE)
            self.assert_tracers_stopped()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)

    def test_teardown_refuses_recycled_tracer_pid(self):
        for role in self.roles:
            (self.d / (role + '.tracer-start')).write_text('0\n')
        result = self.function_probe('stop_helpers\n')
        self.assertNotEqual(result.returncode, 0)

    def test_tracer_kill_removes_real_host_tracee_after_offline(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                self.tracer_kill_probe(cached)

    def tracer_kill_probe(self, cached):
        self.state.write_text('offline\n')
        tracer = subprocess.Popen(['strace', '--kill-on-exit', '-o', str(self.root / 'host.trace'),
                                   '/run/current-system/sw/bin/sleep', '60'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            tracee = None
            deadline = time.monotonic() + 3
            while tracee is None and time.monotonic() < deadline:
                if tracer.poll() is not None:
                    _, error = tracer.communicate()
                    if b'PTRACE_TRACEME: Operation not permitted' in error:
                        self.skipTest('host sandbox denies ptrace; rerun this probe outside the sandbox')
                    self.fail('host strace failed: ' + error.decode())
                for entry in Path('/proc').glob('[0-9]*'):
                    try:
                        fields = (entry / 'stat').read_text().rsplit(') ', 1)[1].split()
                        if fields[1] == str(tracer.pid):
                            tracee = entry
                    except (OSError, IndexError):
                        pass
                time.sleep(0.01)
            self.assertIsNotNone(tracee, 'host strace did not launch its synthetic sleep tracee')
            (self.d / 'rmtfs.tracer').write_text(str(tracer.pid))
            fields = Path(f'/proc/{tracer.pid}/stat').read_text().rsplit(') ', 1)[1].split()
            (self.d / 'rmtfs.tracer-start').write_text(fields[19])
            child_start = (tracee / 'stat').read_text().rsplit(') ', 1)[1].split()[19]
            if cached:
                (self.d / 'rmtfs.identity').write_text(f'{tracer.pid} {fields[19]} {tracee.name} {child_start}\n')
            else:
                (self.d / 'rmtfs.identity').unlink(missing_ok=True)
            result = self.function_probe('ROLES=rmtfs\nstop_modem\nstop_helpers\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            tracer.communicate(timeout=3)
            self.assertEqual(tracer.returncode, -9)
            def tracee_state():
                try:
                    record = (tracee / 'stat').read_text().rsplit(') ', 1)[1].split()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return record[0] if record[19] == child_start else None
            deadline = time.monotonic() + 3
            while tracee_state() not in (None, 'Z') and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(tracee_state(), (None, 'Z'))
        finally:
            if tracer.poll() is None:
                tracer.kill()
            tracer.communicate(timeout=3)

    def test_bad_preparation_cannot_start(self):
        self.state.write_text('offline\n')
        result = self.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        self.assertFalse((self.d / 'start-attempted').exists())

    def test_runtime_rejects_arbitrarily_selected_strace_or_iw_hash(self):
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'wrong-tool-pin.sh'
        for name in ('strace', 'iw'):
            with self.subTest(name=name):
                probe.write_text(functions + '\ntool ' + name + ' ' + '0' * 64 + '\n')
                result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('does not match the pinned static tool', result.stderr)

    def test_prepare_uses_shared_ram_data_admission(self):
        source = self.script.read_text()
        self.assertIn('. /vendor/bin/a9ml-test-data.sh', source)
        self.assertIn("a9ml_test_data || die", source)
        proc = self.root/'guard-proc'; (proc/'self').mkdir(parents=True)
        guard = REPO/'scripts/mainline/android/vendor/bin/a9ml-test-data.sh'
        probe = self.root/'ram-data-test.sh'
        probe.write_text('. '+str(guard)+'\na9ml_test_data '+str(proc)+' /unused /data\n')
        for device, fs, mounted_source, expected in (
                ('1:0','ext4','/dev/block/ram0',True),
                ('1:0','ext4','/dev/ram0',True),
                ('8:0','ext4','/dev/block/ram0',False),
                ('1:0','f2fs','/dev/block/ram0',False)):
            with self.subTest(device=device, fs=fs, source=mounted_source):
                (proc/'self/mountinfo').write_text('32 1 '+device+' / /data rw - '+fs+' '+mounted_source+' rw\n')
                result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, expected, result.stderr)

    def observe_fixture(self):
        # Execute all production observe checks using synthetic proc files.
        self.env.setdefault('FIXTURE_STAT_STYLE', 'toybox')
        proc = self.root / 'proc'
        for role in self.roles:
            (self.d / (role + '.trace')).write_text(valid_trace())
        for tracer, pid, role in ((101, 201, 'rmtfs'), (102, 202, 'tqftpserv'), (103, 203, 'diag-router')):
            (self.d / (role + '.tracer')).write_text(str(tracer))
            (self.d / (role + '.tracer-start')).write_text('1234\n')
            (proc / str(tracer)).mkdir(parents=True)
            (proc / str(tracer) / 'stat').write_text(f'{tracer} (strace) S 1 ' + '0 ' * 17 + '1234\n')
            base = proc / str(pid)
            (base / 'fd').mkdir(parents=True)
            (base / 'stat').write_text(f'{pid} ({role}) S {tracer} ' + '0 ' * 17 + '2345\n')
            (base / 'fdinfo').mkdir()
            (base / 'attr').mkdir()
            (base / 'attr/current').write_text('u:r:a9_wifi_radio:s0\n')
            (base / 'status').write_text('Uid:\t65534\t65534\t65534\t65534\nGid:\t65534\t65534\t65534\t65534\nGroups:\n' +
                                       ''.join(cap + ':\t00000000\n' for cap in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) +
                                       'NoNewPrivs:\t1\nSeccomp:\t2\n')
            (base / 'mountinfo').write_text('1 0 0:1 / / rw,nosuid,nodev,noexec - tmpfs tmpfs rw,noswap\n')
            (base / 'fd/0').touch()  # stat/readlink hooks model /dev/null, no device open.
            os.mkfifo(base / 'fd/1')
            sizes = {10: 2097152, 11: 2097152, 12: 2097152, 13: 0, 14: 0, 15: 131072, 16: 13} if role == 'rmtfs' else (
                    {20: 3814580, 21: 0, 22: 0} if role == 'tqftpserv' else {})
            for fd, size in sizes.items():
                if fd == 16: (base / 'fd/16').symlink_to(self.d / 'rmtfs.admission')
                else:
                    with (base / 'fd' / str(fd)).open('wb') as stream:
                        stream.truncate(size)
                (base / 'fdinfo' / str(fd)).write_text('flags:\t' + ('0100000' if fd in (16,20) else '0100002') + '\n')
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/proc/', str(proc) + '/')
        # Preserve the fixture's stat dialect translation under the hooks.
        functions = functions.replace('stat() {', 'fixture_stat() {', 1)
        probe = self.root / 'observe-test.sh'
        probe.write_text(functions + '''
readlink() {
    case "$1" in
        */101/exe|*/102/exe|*/103/exe) echo "$FIXTURE_TRACE_PATH";;
        */201/exe) echo /vendor/bin/rmtfs;; */202/exe) echo /vendor/bin/tqftpserv;; */203/exe) echo /vendor/bin/diag-router;;
        */fd/0) echo /dev/null;; */fd/13) echo /dev/qcom_rmtfs_mem1;;
        */fd/20) echo /vendor/firmware/qcom/sm6115/hisense/hlte556n/wlanmdsp.mbn;;
        *) command readlink "$@";;
    esac
}
stat() {
    case "$*" in
        '-L -c %n|%F|%s|%d:%i '*)
            shift 3
            inventory=$(fixture_stat -L -c '%n|%F|%s|%d:%i' "$@") || return 1
            printf '%s\\n' "$inventory" | awk -F '|' -v extra="${FIXTURE_EXTRA_KIND:-}" '
                BEGIN {OFS="|"}
                {n=split($1, path, "/"); fd=path[n]
                 if (fd==0 || fd==13) $2="character device"
                 else if (fd==14) $2="socket"
                 else if (fd==99) $2=extra
                 print}'
            return;;
        '-L -f -c %T '*) echo tmpfs; return;;
        '-L -c %F '*)
            case "$4" in
                */fd/0|*/fd/13) if [ "$FIXTURE_STAT_STYLE" = toybox ]; then echo 'character device'; else echo 'character special file'; fi; return;;
                */fd/14) echo socket; return;;
                */fd/99) echo "$FIXTURE_EXTRA_KIND"; return;;
            esac;;
    esac
    fixture_stat "$@"
}
observe
''')
        (self.script.parent / 'audit-opens.awk').write_text((HERE / 'audit-opens.awk').read_text())
        return proc, probe

    def test_observe_accepts_toybox_devices_pipes_and_empty_scratch_but_rejects_block(self):
        proc, probe = self.observe_fixture()
        for style in ('gnu', 'toybox'):
            with self.subTest(style=style):
                env = self.env | {'FIXTURE_STAT_STYLE': style}
                result = subprocess.run([shutil.which('sh'), str(probe)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                extra = proc / '203/fd/99'
                extra.touch()
                for kind in ('block special file', 'block device', 'unknown'):
                    result = subprocess.run([shutil.which('sh'), str(probe)], env=env | {'FIXTURE_EXTRA_KIND': kind},
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('has block FD' if kind.startswith('block') else 'unexpected descriptor type', result.stderr)
                extra.unlink()

    def fresh_fixture(self):
        proc, probe = self.observe_fixture()
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for tracer, pid, role in ((101, 201, 'rmtfs'), (102, 202, 'tqftpserv'), (103, 203, 'diag-router')):
            self.assertEqual((self.d / (role + '.identity')).read_text(), f'{tracer} 1234 {pid} 2345\n')
        functions = probe.read_text().rsplit('\nobserve\n', 1)[0]
        return proc, functions

    def test_freshness_checks_only_verified_processes_among_1000_unrelated_pids(self):
        proc, functions = self.fresh_fixture()
        for pid in range(1000, 2000):
            (proc / str(pid)).mkdir()
            (proc / str(pid) / 'stat').write_text(f'{pid} (unrelated) S 1 ' + '0 ' * 17 + '9999\n')
        functions = functions.replace('read_process_record() {', 'verified_record() {', 1)
        hooks = '''
read_process_record() { echo "$1" >> "$D/record-reads"; verified_record "$1"; }
trace_children() { echo forbidden-full-scan >&2; return 1; }
'''
        started = time.monotonic()
        result = self.function_probe(hooks + '\nfreshness\n', functions)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.d / 'record-reads').read_text().split(), ['101', '201', '102', '202', '103', '203'])
        print(f'freshness budget: {elapsed:.4f}s host wall; 6 process reads, 1000 unrelated PIDs, zero topology scans', flush=True)

    def test_freshness_rejects_recycled_reparented_reexeced_or_faulted_companion(self):
        proc, functions = self.fresh_fixture()
        cases = [
            (self.d / 'rmtfs.identity', b'101 1234 201 2345 unexpected\n'),
            (self.d / 'rmtfs.identity', b'101 1234 x 2345\n'),
            (self.d / 'rmtfs.tracer', b'999\n'),
            (self.d / 'rmtfs.tracer-start', b'999\n'),
            (proc / '101/stat', b'101 (strace) S 1 ' + b'0 ' * 17 + b'999\n'),
            (proc / '201/stat', b'201 (rmtfs) S 999 ' + b'0 ' * 17 + b'2345\n'),
            (proc / '201/stat', b'201 (rmtfs) S 101 ' + b'0 ' * 17 + b'999\n'),
            (proc / '201/stat', b'201 (rmtfs) Z 101 ' + b'0 ' * 17 + b'2345\n'),
            (self.d / 'rmtfs.trace', b''),
            (self.d / 'rmtfs.log', b'A9_WIFI_FAULT rmtfs synthetic\n'),
            (self.d / 'rmtfs.exit', b'1\n'),
        ]
        for path, bad in cases:
            with self.subTest(path=path, bad=bad):
                original = path.read_bytes() if path.exists() else None
                path.write_bytes(bad)
                result = self.function_probe('freshness\n', functions)
                self.assertNotEqual(result.returncode, 0)
                if original is None: path.unlink()
                else: path.write_bytes(original)
        self.assertNotEqual(self.function_probe('freshness\n', functions.replace('echo /vendor/bin/rmtfs', 'echo /vendor/bin/unreviewed')).returncode, 0)
        (self.d / 'rmtfs.identity').unlink()
        self.assertNotEqual(self.function_probe('freshness\n', functions).returncode, 0)

    def start_fixture(self, extra_hooks=''):
        proc, functions = self.fresh_fixture()
        self.state.write_text('offline\n');self.allow_start = True
        (self.d / 'stage').write_text('prepared\n')
        (self.d / 'guard-armed.uptime').write_text('0\n')
        (self.d / 'cleanup-budget-verified').write_text('10\n')
        for name in ('watchdog-ready', 'guardian-ready'):
            (self.d / name).touch()
        for name in ('watchdog', 'guardian'):  # live pids for the release watcher's kill -0
            (self.d / (name + '.pid')).write_text(str(os.getpid()) + '\n')
        (self.d / 'diag-router.log').write_text('A9_WIFI_READY diag-router\nA9_WIFI_LOCAL_NODE 7\nA9_WIFI_DIAG_HANDSHAKE\n')
        hooks = '''
monitors() { [ ! -e "$D/fault" ] && [ ! -e "$D/abort" ] && [ ! -e "$D/complete" ] && [ ! -e "$D/fixture-monitor-dead" ]; }
diag_lookup() { :; }
services() { :; }
observe() { die full-observe-inside-start; }

'''
        stages = self.script.read_text().split('case "${1:-}" in', 1)[1]
        self.script.write_text(functions + hooks + extra_hooks + '\ncase "${1:-}" in' + stages)
        return proc

    def test_start_refreshes_after_lookup_and_refuses_changed_state_or_guards(self):
        for change in ('exit', 'recovery', 'state', 'abort', 'monitor'):
            with self.subTest(change=change):
                # Each case gets an independent full-observe cache/fixture.
                fixture = Controller(methodName='runTest');fixture.setUp()
                try:
                    actions = {'exit': 'echo 1 > "$D/rmtfs.exit"',
                               'recovery': f'echo enabled > "{fixture.state.parent}/recovery"',
                               'state': f'echo running > "{fixture.state}"',
                               'abort': 'touch "$D/abort"', 'monitor': 'touch "$D/fixture-monitor-dead"'}
                    fixture.start_fixture('\ndiag_lookup() { ' + actions[change] + '; }\n')
                    result = fixture.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((fixture.d / 'start-attempted').exists())
                finally:
                    fixture.doCleanups()

    def test_start_refuses_changed_firmware_before_attempt_or_release(self):
        for change in ('path', 'missing-mdt'):
            with self.subTest(change=change):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.start_fixture()
                    if change == 'path':
                        (f.root/'sys/module/firmware_class/parameters/path').write_text('/data/local/tmp/a9fw\n')
                    else:
                        (f.root/'vendor/firmware/qcom/sm6115/hisense/hlte556n/modem.mdt').unlink()
                    r = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
                    self.assertNotEqual(r.returncode, 0, r.stdout+r.stderr)
                    self.assertIn('firmware bridge changed', r.stderr)
                    self.assertFalse((f.d/'start-attempted').exists())
                    self.assertFalse((f.d/'start-write-pending').exists())
                    self.assertEqual((f.d/'rmtfs.admission').read_bytes(), b'A9RMTFS1:0:0\n')
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                finally: f.doCleanups()

    def test_start_budget_refuses_slow_freshness_missing_future_or_malformed_anchor(self):
        for anchor, now in ((None, 0), ('bad\n', 0), ('1\n', 0), ('0\n', 19), ('0\n', 20), ('0\n', 28), ('0\n', 29)):
            with self.subTest(anchor=anchor, now=now):
                fixture = Controller(methodName='runTest');fixture.setUp()
                try:
                    fixture.start_fixture()
                    if anchor is None: (fixture.d / 'guard-armed.uptime').unlink()
                    else: (fixture.d / 'guard-armed.uptime').write_text(anchor)
                    (fixture.root / 'uptime').write_text(f'{now}.0 0.0\n')
                    result = fixture.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(any(x in result.stderr for x in ('budget', 'anchor unavailable', 'bounded activation')), result.stderr)
                    self.assertFalse((fixture.d / 'start-attempted').exists())
                finally:
                    fixture.doCleanups()
        fixture = Controller(methodName='runTest');fixture.setUp()
        try:
            fixture.start_fixture('\ndiag_lookup() { echo "29.0 0.0" > "' + str(fixture.root / 'uptime') + '"; }\n')
            result = fixture.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(any(x in result.stderr for x in ('budget', 'anchor unavailable', 'bounded activation')), result.stderr)
            self.assertFalse((fixture.d / 'start-attempted').exists())
        finally:
            fixture.doCleanups()
        self.start_fixture()
        (self.root / 'uptime').write_text('18.0 0.0\n')
        result = self.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.d / 'start-attempted').exists())

    def test_start_rebuilds_three_descriptor_inventories_on_both_sides_of_diag(self):
        self.start_fixture('''
verified_inventory_stat() { observed_inventory_stat "$@"; }
stat() {
    case "$*" in '-L -c %n|%F|%s|%d:%i '*) echo "$4" >> "$D/inventory-calls";; esac
    verified_inventory_stat "$@"
}
''')
        # The fixture models descriptor types, while the production gate,
        # snapshot lookups, real auditor and both freshness passes execute.
        text = self.script.read_text().replace('\nstat() {', '\nobserved_inventory_stat() {', 1)
        self.script.write_text(text)
        result = self.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START', timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.d / 'inventory-calls').read_text().splitlines()
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[:3], calls[3:])
        labels = [line.split()[1] for line in result.stdout.splitlines() if line.startswith('A9_WIFI_TIMING ')]
        self.assertEqual(labels[:6], ['start-gate-before-begin', 'start-gate-before-end',
                                     'start-diag-begin', 'start-diag-end',
                                     'start-gate-after-begin', 'start-gate-after-end'])

    def test_cached_teardown_is_offline_ordered_without_host_wide_scan(self):
        proc, functions = self.fresh_fixture()
        hooks = '''
kill() {
    [ "$(cat "''' + str(self.state) + '''")" = offline ] || return 1
    printf '%s\n' "$*" >> "$D/kills"
    if [ "$1" = -STOP ]; then
        sed 's/) S /) T /' "''' + str(proc) + '''/$2/stat" > "$D/stat-next"
        mv "$D/stat-next" "''' + str(proc) + '''/$2/stat"
    fi
}
trace_children() { echo unexpected-full-scan >&2; return 1; }
'''
        self.state.write_text('offline\n')
        result = self.function_probe(hooks + '\nstop_helpers\n', functions)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.d / 'kills').read_text().splitlines(),
                         [f'{sig} {pid}' for tracer, child in ((101,201),(102,202),(103,203))
                          for sig, pid in (('-STOP',tracer),('-KILL',child),('-KILL',tracer))])

    def test_cached_teardown_never_kills_recycled_child(self):
        proc, functions = self.fresh_fixture()
        (proc / '201/stat').write_text('201 (other) S 101 ' + '0 ' * 17 + '999\n')
        hooks = '''
kill() { echo "$*" >> "$D/kills"; }
process_record() { case "$1" in 101) echo '1 1234 T';; 201) echo '101 999 S';; *) return 1;; esac; }
trace_children() { return 99; }
'''
        result = self.function_probe(hooks + '\nROLES=rmtfs\nstop_helpers\n', functions)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.d / 'kills').read_text(), '-STOP 101\n')

    def test_check_keeps_immutable_observe_outside_but_refreshes_mutable_gates(self):
        _, functions = self.fresh_fixture()
        (self.d / 'stage').write_text('started\n')
        (self.d / 'guard-armed.uptime').write_text('0\n')
        (self.d / 'started.uptime').write_text('0\n')
        wlan = self.root / 'sys/class/net/wlan0'
        node = wlan / 'device/of_node'
        node.mkdir(parents=True)
        # Synthetic fixture only; no real device identity is read or recorded.
        address = bytes((2, 0, 0, 0, 0, 1))
        (wlan / 'address').write_text(':'.join(f'{x:02x}' for x in address) + '\n')
        (node / 'local-mac-address').write_bytes(address)
        hooks = '''
observe() { die running-full-observe; }

monitors() { return 0; }
services() { return 0; }
kernel_evidence() { :; }
capture_identity() { :; }
scan_if_reviewed() { :; }
sleep() {
    read -r model_now model_idle < "''' + str(self.root / 'uptime') + '''"
    model_now=${model_now%%.*}
    echo "$((model_now + 3)).0 0.0" > "''' + str(self.root / 'uptime') + '''"
}
'''
        stages = self.script.read_text().split('case "${1:-}" in', 1)[1]
        stages = stages.replace('/sys/class/net/wlan0', str(wlan))
        self.script.write_text(functions + hooks + '\ncase "${1:-}" in' + stages)
        result = self.run_stage('check', timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.d / 'stage').read_text().strip(), 'captured')
        self.assertEqual((self.root / 'uptime').read_text().split()[0], '180.0')
        self.assertFalse((self.d / 'scan-attempted').exists())

    def test_mutable_descriptors_and_auditor_are_fresh_before_start_and_after_diag(self):
        for when in ('before', 'during-diag'):
            for change in ('close', 'shape', 'flags', 'alias', 'extra', 'trace'):
                with self.subTest(when=when, change=change):
                    f = Controller(methodName='runTest'); f.setUp()
                    try:
                        proc = f.start_fixture()
                        actions = {
                            'close': f'rm "{proc}/201/fd/10"',
                            'shape': f'truncate -s 1 "{proc}/201/fd/10"',
                            'flags': f'echo "flags: 0100000" > "{proc}/201/fdinfo/10"',
                            'alias': f'rm "{proc}/201/fd/11"; ln "{proc}/201/fd/10" "{proc}/201/fd/11"',
                            'extra': f'touch "{proc}/201/fd/99"',
                            'trace': '''echo '123 openat(AT_FDCWD, "/dev/block/by-name/modemst1", O_RDONLY) = -1 EPERM (denied)' >> "$D/rmtfs.trace"''',
                        }
                        if when == 'before':
                            f.function_probe(actions[change] + '\n')
                        else:
                            text = f.script.read_text().replace('diag_lookup() { :; }', 'diag_lookup() { ' + actions[change] + '; }')
                            f.script.write_text(text)
                        r = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START', timeout=15)
                        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                        self.assertFalse((f.d / 'start-attempted').exists())
                        self.assertEqual(f.state.read_text().strip(), 'offline')
                    finally:
                        f.doCleanups()

    def timed_fixture(self, **costs):
        """Real run/start/check/stop AND both guard cases, synthetic endpoints.

        Only external I/O cost / sleeping is virtual. No controller stage or
        guard is replaced. Real descriptor/auditor/dd/hash/cmp code executes.
        Guard sleeps wait on the shared clock; only the active stage advances
        it. A fake timeout watches that clock AND has a bounded wall backstop.
        """
        proc, functions = self.fresh_fixture()
        self.prepare_fixture_stores()
        shutil.rmtree(self.d / 'stores')  # production run must create its mount
        self.allow_start = True
        (self.d / 'stage').write_text('prepared\n')
        (self.d / 'diag-router.log').write_text('A9_WIFI_READY diag-router\nA9_WIFI_LOCAL_NODE 7\nA9_WIFI_DIAG_HANDSHAKE\n')
        wlan = self.root / 'net/wlan0'
        (wlan / 'device/of_node').mkdir(parents=True)
        address = bytes((2, 0, 0, 0, 0, 1))  # synthetic; never read target IDs
        (wlan / 'address').write_text(':'.join(f'{x:02x}' for x in address) + '\n')
        (wlan / 'device/of_node/local-mac-address').write_bytes(address)
        # Keep monitor command-identity reads against the host's REAL procfs.
        a = functions.index('monitors() {'); b = functions.index('services() {', a)
        functions = functions[:a] + functions[a:b].replace(str(proc) + '/', '/proc/') + functions[b:]
        for name in ('observe', 'audit', 'stores', 'freshness', 'stop_modem', 'diag_lookup', 'services', 'stamp', 'topology'):
            functions = functions.replace(name + '() {', 'verified_' + name + '() {', 1)
            functions = functions.replace(name + '() (', 'verified_' + name + '() (', 1)
        lookup = self.bin / 'qrtr-lookup-fixture'
        lookup.write_text('#!' + shutil.which('sh') + '''
case "${1:-}" in
"") printf '  Service Version Instance Node  Port\n14 1 0 7 14\n4097 N/A 0 7 12289\n4097 N/A 2 7 12290\n4097 N/A 4 7 12291\n';;
4097) printf '4097 N/A 0 7 12289\n4097 N/A 2 7 12290\n4097 N/A 4 7 12291\n';;
64|69) echo "$1 1 0 7 12345";;
esac
''')
        lookup.chmod(0o700)
        functions = functions.replace('/vendor/bin/qrtr-lookup', str(lookup))
        # A separate process watches virtual time; production timeout arguments
        # are left intact and recorded. The same fixture runs with real Android
        # timeout separately in test_timeout_kills_descendant_before_late_write.
        timer = self.bin / 'timeout'
        timer.write_text('''#!''' + shutil.which('python3') + '''
import os, pathlib, signal, subprocess, sys, time
args=sys.argv[1:]
foreground=bool(args and args[0]=='--foreground')
if foreground: args=args[1:]
if args[:2]==['-s','KILL']: args=args[2:]
seconds=float(args.pop(0)); clock=pathlib.Path(os.environ['FIXTURE_CLOCK'])
def now(): return float(clock.read_text().split()[0])
end=now()+seconds
p=subprocess.Popen(args,start_new_session=not foreground)
wall=time.monotonic()+float(os.environ.get('FIXTURE_WALL_LIMIT','30'))
while p.poll() is None:
    if now()>=end or time.monotonic()>=wall:
        (os.kill if foreground else os.killpg)(p.pid,signal.SIGKILL); p.wait(); sys.exit(124)
    time.sleep(.002)
sys.exit(p.returncode)
''')
        timer.chmod(0o700)
        self.env.update(FIXTURE_CLOCK=str(self.root / 'uptime'),
                        FIXTURE_WALL_LIMIT=str(30 * TIMEOUT_SCALE))
        defaults = dict(proof=157, pre_store=1, post_store=1, gate=1, diag=1,
                        stop=1, final=0, startup=0, pre_audit=0, post_audit=0,
                        guard_launch=0, admission=0, post_audit_gap=0,
                        post_proof_gap=0, arm_gap=0, diag_gap=0,
                        after_gate_gap=0, freshness_gap=0, topology=0)
        defaults.update(costs)
        defaults.setdefault('gate_after', defaults['gate'])
        self.env.update({'COST_' + k.upper(): str(v) for k, v in defaults.items()})
        hooks = r'''
model_stage=${1:-}
cat() {
    if [ "$#" = 1 ] && [ "$1" = "STATE_NODE" ]; then
        value=
        while [ -z "$value" ]; do value=$(command cat "$1"); done
        # A regular-file fixture exposes writes; real sysfs reads never return
        # the literal command text or a truncate/write gap.
        case "$value" in start) echo offline;; stop) echo running;; *) echo "$value";; esac
    else command cat "$@"; fi
}
advance() {
    read -r model_now model_idle < "$FIXTURE_CLOCK"
    # Millisecond fixed point lets phone measurements retain their fractions.
    # Prefix fractions with 1 to avoid shell octal interpretation (e.g. .08).
    model_fraction=${model_now#*.}000
    model_fraction=${model_fraction%"${model_fraction#???}"}
    model_now=$(( ${model_now%%.*} * 1000 + 1$model_fraction - 1000 ))
    model_cost_whole=${1%%.*}; model_cost_fraction=000
    case "$1" in *.*)
        model_cost_fraction=${1#*.}000
        model_cost_fraction=${model_cost_fraction%"${model_cost_fraction#???}"};;
    esac
    model_now=$((model_now + model_cost_whole * 1000 + 1$model_cost_fraction - 1000))
    printf '%s.%03d 0.0\n' "$((model_now / 1000))" "$((model_now % 1000))" > "$D/clock-$$"
    mv "$D/clock-$$" "$FIXTURE_CLOCK"
}
stamp() {
    [ "$1" != start-gate-before-begin ] || advance "$COST_GUARD_LAUNCH"
    verified_stamp "$1"
    case "$1" in
        calibration-audit-end) advance "$COST_POST_AUDIT_GAP";;
        offline-audit-end) advance "$COST_POST_PROOF_GAP";;
        stores-pre-end) advance "$COST_ARM_GAP";;
        start-gate-before-end) advance "$COST_DIAG_GAP";;
        start-diag-end) advance "$COST_AFTER_GATE_GAP";;
        start-gate-after-end) advance "$COST_FRESHNESS_GAP";;
        start-freshness-end) advance "$COST_ADMISSION";;
    esac
}
sleep() {
    case "$model_stage" in
        --guardian)
            clock_now; wait_until=$((clock_value + $1))
            while :; do clock_now; [ "$clock_value" -lt "$wait_until" ] || break; command sleep 0.005; done;;
        --watchdog) command sleep 0.005;;
        --check-body) advance "$1"; command sleep 0.005;;
        *) command sleep 0.005;;
    esac
}
mount() { :; }
observe() { verified_observe; [ "$model_stage" != --offline-proof ] || advance "$COST_PROOF"; }
topology() { verified_topology "$1" || return; [ "$1" != after ] || advance "$COST_TOPOLOGY"; }
audit() {
    verified_audit || return 1
    case "$model_stage" in
        --offline-proof) advance "$COST_PRE_AUDIT";;
        --stop-tail) advance "$COST_POST_AUDIT";;
        --start-guarded)
            model_gate_count=$((${model_gate_count:-0} + 1))
            if [ "$model_gate_count" = 1 ]; then advance "$COST_GATE"; else advance "$COST_GATE_AFTER"; fi;;
    esac
}
stores() {
    verified_stores "$1" || return 1
    case "$1" in pre) advance "$COST_PRE_STORE";; post) advance "$COST_POST_STORE";; esac
}
freshness() { verified_freshness; }
diag_lookup() { verified_diag_lookup; [ "$model_stage" != --start-guarded ] || advance "$COST_DIAG"; }
services() {
    verified_services || return 1
    [ "$model_stage" != --check-final ] || advance "$COST_FINAL"
}
stop_modem() {
    verified_stop_modem
    # A redundant independent stop of an already-offline modem has no modeled
    # hardware shutdown delay; the ordinary stop phase retains its full cost.
    [ "$model_stage" = --guardian-stop ] || advance "$COST_STOP"
}
kill() {
    case "$2" in
        101|102|103|201|202|203)
            [ "$1" != -0 ] || return 0
            if [ "$1" = -USR1 ]; then echo "$2" >> "$D/usr1"; return 0; fi
            offline || return 1
            printf '%s\n' "$*" >> "$D/kills"
            case "$1" in -STOP) model_state=T;; -KILL) model_state=Z; advance 1;; *) return 99;; esac
            sed "s/) [A-Z] /) $model_state /" "PROC/$2/stat" > "$D/stat-$$"
            mv "$D/stat-$$" "PROC/$2/stat";;
        *) command kill "$@";;
    esac
}
kernel_evidence() { printf '%s\n' 'IDENTITY' > "$D/kernel-identity.txt"; }
'''.replace('PROC/', str(proc) + '/').replace('STATE_NODE', str(self.state)).replace('IDENTITY', self.identity_log().rstrip())
        # The firmware write itself is synthetic, but its delay/return is real
        # I/O to the temporary state node through the production timeout call.
        hooks += '''
if [ "$model_stage" = --start-guarded ]; then
    # Startup-cost injection happens in the actual sysfs write's subprocess.
    :
fi
'''
        stages = self.script.read_text().split('case "${1:-}" in', 1)[1]
        stages = stages.replace('/sys/class/net/wlan0', str(wlan))
        stages = stages.replace("'echo start > \"$1\"", "'read n idle < \"$FIXTURE_CLOCK\"; frac=${n#*.}; n=${n%%.*}; echo \"$((n + COST_STARTUP)).$frac 0.0\" > \"$FIXTURE_CLOCK-start\"; mv \"$FIXTURE_CLOCK-start\" \"$FIXTURE_CLOCK\"; echo start > \"$1\"")
        self.script.write_text(functions + hooks + '\ncase "${1:-}" in' + stages)
        self.addCleanup(self.kill_fixture_monitors)
        return proc

    def kill_fixture_monitors(self):
        for name in ('guardian', 'watchdog', 'guardian-stop'):
            path = self.d / (name + '.pid')
            if path.exists():
                try:
                    pid = int(path.read_text())
                    cmd = Path(f'/proc/{pid}/cmdline').read_bytes()
                    if str(self.script).encode() in cmd:
                        os.kill(pid, 9)
                except (OSError, ValueError):
                    pass

    def timed_run(self, timeout=30):
        return self.run_stage('run', 'I_AUTHORIZE_ONE_MPSS_START',
                              'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64, timeout=timeout)

    # ---- integrated Wi-Fi session (run --session): runs/wifi-android-plan.md ----
    SESSION_RUN = ('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64)

    def test_session_options_are_bounded_paired_and_exclusive(self):
        digest = 'a' * 64
        cases = [(['--session', '599', '--identity-reviewed', digest], 'session length must be 600..14400 s'),
                 (['--session', '14401', '--identity-reviewed', digest], 'session length must be 600..14400 s'),
                 (['--session', 'x', '--identity-reviewed', digest], 'invalid session length'),
                 (['--session', '3600'], '--session and --identity-reviewed go together'),
                 (['--identity-reviewed', digest], '--session and --identity-reviewed go together'),
                 (['--session', '3600', '--identity-reviewed', 'A' * 64], 'invalid reviewed identity digest'),
                 (['--session', '3600', '--identity-reviewed', 'a' * 63], 'reviewed identity digest length'),
                 (['--session', '3600', '--identity-reviewed', digest, '--android'], 'exclusive'),
                 (['--session', '3600', '--identity-reviewed', digest, '--observe-wlan'], 'exclusive'),
                 (['--session', '3600', '--identity-reviewed', digest, '--scan-reviewed', digest], 'exclusive')]
        for extra, message in cases:
            with self.subTest(extra=extra):
                r = self.run_stage(*self.SESSION_RUN, *extra)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)
                self.assertFalse((self.d / 'start-attempted').exists())
                self.assertFalse((self.d / 'session-total').exists())

    def test_total_length_defaults_to_240_and_session_value_is_validated_everywhere(self):
        probe = 'rc=0; total_load || rc=$?; echo "rc=$rc TOTAL=$TOTAL"\nrc=0; active_end || rc=$?; echo "active-rc=$rc"\n'
        (self.d / 'guard-armed.uptime').write_text('100\n')
        (self.root / 'uptime').write_text('105.0 0.0\n')
        r = self.function_probe(probe)
        self.assertIn('rc=0 TOTAL=240', r.stdout)
        self.assertIn('\n315\n', r.stdout)   # 100 + 240 - 5 - 20: the unchanged trial deadline
        for value, ok, active in (('3600', True, 3675), ('240', True, 315), ('14400', True, 14475),
                                  ('239', False, 0), ('14401', False, 0), ('36x0', False, 0), ('3600 7', False, 0), ('', False, 0)):
            with self.subTest(value=value):
                (self.d / 'session-total').write_text(value + '\n')
                r = self.function_probe(probe)
                if ok:
                    self.assertIn('rc=0 TOTAL=' + value, r.stdout)
                    self.assertIn('\n%d\n' % active, r.stdout)
                else:
                    self.assertIn('rc=1', r.stdout, r.stdout + r.stderr)
                    if value.isdigit(): self.assertNotIn('\n%d\n' % (100 + int(value)), r.stdout)
        (self.d / 'session-total').unlink()
        (self.d / 'session-total').symlink_to(self.root / 'uptime')   # a link is refused
        self.assertIn('rc=1', self.function_probe(probe).stdout)

    def test_every_guard_reads_the_total_and_no_fixed_240_270_remains_outside_comments(self):
        text = (HERE / 'coordinator-target.sh').read_text()
        code = [l for l in text.splitlines() if not l.strip().startswith('#')]
        for number in ('240', '270'):
            hits = [l.strip() for l in code if re.search(r'(\+|-) *' + number + r'\b', l)]
            self.assertEqual(hits, [], 'hard-coded %s s left in code: %s' % (number, hits))
        for guard in ('--guardian)', '--watchdog)'):
            body = text.split('\n    ' + guard, 1)[1].split('\n    --', 1)[0]
            self.assertIn('total_load || fallback', body, guard)

    def test_session_identity_accepts_board_id_ff_only_when_asked(self):
        log = self.identity_log().replace('board_id 0x002', 'board_id 0xff')
        strict = self.identity_probe(log)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn('default QMI identity', strict.stderr + strict.stdout)
        relaxed = self.identity_probe(log, 'ID_BOARD_FF=1 capture_identity\n')
        self.assertEqual(relaxed.returncode, 0, relaxed.stderr)
        self.assertIn('board_id=0xff\n', (self.d / 'identity.txt').read_text())
        # chip_id 0xff stays refused in every mode
        bad = self.identity_probe(self.identity_log().replace('chip_id 0x01', 'chip_id 0xff'), 'ID_BOARD_FF=1 capture_identity\n')
        self.assertNotEqual(bad.returncode, 0)

    def test_session_regulatory_requires_a_country_reloads_applies_and_verifies_it(self):
        (self.root / 'vendor-session').mkdir()
        iw = self.root / 'mnt/a9-wifi/tools/iw'
        iw.write_text('#!' + shutil.which('sh') + '''
echo "iw $*" >> "$FIXTURE_COMMAND_LOG"
case "$1 $2" in
 "reg reload") exit ${FIXTURE_RELOAD_RC:-0};;
 "reg set") exit ${FIXTURE_SET_RC:-0};;
 "reg get") echo global; echo "country ${FIXTURE_COUNTRY:-00}: DFS-UNSET"; echo "	(2402 - 2472 @ 40), (N/A, 20), (N/A)";;
esac
''')
        iw.chmod(0o700)
        functions = self.script.read_text().split('case "${1:-}" in')[0].replace('/vendor/etc/a9-wifi/session', str(self.root / 'vendor-session'))
        cases = ((None, '00', {}, False), ('US', 'US', {}, True), ('US', '00', {}, False), ('US', 'CN', {}, False),
                 ('US', 'US', {'FIXTURE_RELOAD_RC': '1'}, False), ('US', 'US', {'FIXTURE_SET_RC': '1'}, False),
                 ('us', 'US', {}, False), ('USA', 'US', {}, False))
        for country, active, extra, ok in cases:
            with self.subTest(country=country, active=active, extra=extra):
                self.commandlog.unlink(missing_ok=True)
                cfile = self.root / 'vendor-session/country'
                cfile.unlink(missing_ok=True)
                if country is not None: cfile.write_text(country + '\n')
                self.env.update({'FIXTURE_COUNTRY': active, 'FIXTURE_RELOAD_RC': '0', 'FIXTURE_SET_RC': '0'}); self.env.update(extra)
                r = self.function_probe('session_regulatory\n', functions)
                self.assertEqual(r.returncode == 0, ok, r.stdout + r.stderr)
                log = self.commandlog.read_text() if self.commandlog.exists() else ''
                if ok:
                    self.assertLess(log.index('iw reg reload'), log.index('iw reg set US'))
                if country is None or country in ('us', 'USA'): self.assertNotIn('iw reg', log)
                if extra.get('FIXTURE_RELOAD_RC'): self.assertNotIn('iw reg set', log)   # no country hint on a failed reload
                self.assertNotRegex(r.stdout, r'([0-9a-f]{2}:){5}[0-9a-f]{2}')

    def session_overrides(self, root):
        """Fixture commands for the session body (virtual clock); returns the script text to insert."""
        (root / 'vendor-session').mkdir(exist_ok=True)
        (root / 'vendor-session/android-bundle.json').write_text('{}\n')
        (root / 'vendor-session/country').write_text('US\n')
        iw = self.root / 'mnt/a9-wifi/tools/iw'
        iw.write_text('#!' + shutil.which('sh') + '''
echo "iw $*" >> "$FIXTURE_COMMAND_LOG"
case "$1 $2" in "reg get") echo global; echo "country US: DFS-FCC";; esac
''')
        iw.chmod(0o700)
        cmd = self.bin / 'cmd-fixture'
        cmd.write_text('#!' + shutil.which('sh') + '\nprintf "%s\\n" "cmd $*" >> "$FIXTURE_COMMAND_LOG"\n')
        cmd.chmod(0o700)
        return '''
getprop() {
    case "$1" in
        ro.a9ml.vendor|ro.a9ml.wifi) echo 1;; ro.boot.slot_suffix) echo _b;; ro.a9ml.wifi.pd_mapper) echo kernel;;
        vendor.a9ml.wifi.session.stop)
            if [ -n "${FIXTURE_KILL_AFTER:-}" ]; then
                polls=$(command cat "$FIXTURE_ROOT/polls" 2>/dev/null || echo 0); polls=$((polls + 1)); echo "$polls" > "$FIXTURE_ROOT/polls"
                [ "$polls" -ne "$FIXTURE_KILL_AFTER" ] || echo 70 > "$D/tqftpserv.exit"
            fi
            command cat "$FIXTURE_ROOT/prop-stop" 2>/dev/null || :;;
        init.svc.vendor.a9ml_wifi_hal|init.svc.wpa_supplicant)
            # running from the gate opening until the controller closes it (init would stop them)
            if grep -q 'android.ready 0' "$FIXTURE_COMMAND_LOG" 2>/dev/null; then echo stopped; else echo running; fi;;
    esac
}
sleep() {
    case "$model_stage" in
        --guardian)
            clock_now; wait_until=$((clock_value + $1))
            while :; do clock_now; [ "$clock_value" -lt "$wait_until" ] || break; command sleep 0.005; done;;
        --watchdog) command sleep 0.005;;
        --check-body|--session-body) advance "$1"; command sleep 0.005;;
        *) command sleep 0.005;;
    esac
}
'''

    def session_fixture(self, **costs):
        self.timed_fixture(**costs)
        root = self.root
        self.env['FIXTURE_ROOT'] = str(root)
        text = self.script.read_text()
        text = text.replace('/vendor/etc/a9-wifi/android-bundle.json', str(root / 'vendor-session/android-bundle.json'))
        text = text.replace('/vendor/etc/a9-wifi/session', str(root / 'vendor-session'))
        text = text.replace('/system/bin/cmd', str(self.bin / 'cmd-fixture'))
        marker = '\ncase "${1:-}" in'
        head, tail = text.split(marker, 1)
        self.script.write_text(head + self.session_overrides(root) + marker + tail)
        # the digest the image would carry: the canonical identity of the fixture log, board_id rule on
        probe = self.identity_probe(self.identity_log(), 'ID_BOARD_FF=1 capture_identity\n')
        self.assertEqual(probe.returncode, 0, probe.stderr)
        digest = (self.d / 'identity.sha256').read_text().strip()
        for name in ('identity.sha256', 'identity.txt', 'kernel-identity.txt'):
            (self.d / name).unlink()
        return digest

    def session_run(self, digest, seconds=600, timeout=60):
        return self.run_stage(*self.SESSION_RUN, '--session', str(seconds), '--identity-reviewed', digest, timeout=timeout)

    def test_session_runs_to_its_length_then_stops_orderly_with_no_store_open(self):
        digest = self.session_fixture()
        r = self.session_run(digest)
        fault = (self.d / 'fault').read_text() if (self.d / 'fault').exists() else ''
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + fault)
        marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')}
        arm = marks['guards-arm']
        self.assertEqual((self.d / 'session-end').read_text().strip(), 'session-length')
        self.assertEqual((self.d / 'session-total').read_text().strip(), '600')
        self.assertTrue((self.d / 'complete').exists())
        self.assertFalse((self.d / 'fault').exists(), fault)
        self.assertIn('A9_WIFI_SESSION serving', r.stdout)
        self.assertIn('A9_WIFI_SESSION regulatory begin', r.stdout)
        # no live-store open at all: neither the audit mount nor any read of the by-name nodes
        self.assertFalse((self.d / 'stores').exists())
        self.assertNotIn('stores-pre', r.stdout)
        self.assertNotIn('PRE_STORE_SHA256', r.stdout)
        self.assertEqual((self.d / 'stores-post-complete').read_text().strip(), 'skipped')
        # the session ends before the unchanged cleanup reserve; MPSS offline long before arm+TOTAL-5
        self.assertGreaterEqual(marks['stop-request'] - arm, 500)
        self.assertLess(marks['modem-offline'] - arm, 600 - 5)
        self.assertLess(marks['stop-evidence-complete'] - arm, 600 + 30 - 5)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        log = self.commandlog.read_text()
        self.assertIn('setprop vendor.a9ml.wifi.android.ready 1', log)
        self.assertLess(log.index('android.ready 1'), log.index('android.ready 0'))   # gate opens, then closes at stop
        self.assertIn('cmd wifi set-wifi-enabled disabled', log)
        self.assertLess(log.index('set-wifi-enabled disabled'), log.index('android.ready 0'))
        self.assertIn('iw reg reload', log)
        # the real guards (guardian 270 -> TOTAL+30) were armed with the session length
        self.assertTrue((self.d / 'guardian.pid').exists() and (self.d / 'watchdog.pid').exists())

    def test_session_stop_property_ends_it_orderly_and_a_wrong_identity_never_opens_the_gate(self):
        digest = self.session_fixture()
        (self.root / 'prop-stop').write_text('1\n')
        r = self.session_run(digest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.d / 'session-end').read_text().strip(), 'requested')
        self.assertTrue((self.d / 'complete').exists())

    def test_session_identity_mismatch_fails_before_the_android_gate(self):
        digest = self.session_fixture()
        r = self.session_run('0' * 64 if digest != '0' * 64 else '1' * 64)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('differs from the reviewed one', r.stdout + r.stderr)
        self.assertNotIn('android.ready 1', self.commandlog.read_text() if self.commandlog.exists() else '')
        self.assertTrue((self.d / 'complete').exists())   # still stopped orderly, MPSS offline
        self.assertEqual(self.state.read_text().strip(), 'offline')

    def test_session_companion_death_is_a_failed_session_with_modem_stopped_and_no_retry(self):
        digest = self.session_fixture()
        self.env['FIXTURE_KILL_AFTER'] = '5'    # tqftpserv "exits" on the fifth poll of the serving loop
        r = self.session_run(digest)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertNotIn('A9_WIFI_SESSION ending', r.stdout)           # a fault, not an orderly end
        self.assertEqual(self.state.read_text().strip(), 'offline')    # modem stopped
        self.assertTrue((self.d / 'complete').exists())
        self.assertTrue((self.d / 'fault').exists())
        log = self.commandlog.read_text()
        self.assertIn('android.ready 0', log)                          # framework gate closed first
        self.assertEqual(log.count('android.ready 1'), 1)              # one release, never re-opened
        self.assertEqual((self.d / 'start-attempted').read_text().strip(), 'attempted')
        # no retry: a second run on the same boot is refused before anything starts
        again = self.session_run(digest)
        self.assertNotEqual(again.returncode, 0)
        self.assertRegex(again.stderr + again.stdout, 'prepare and review offline confinement first|one trial per fresh boot')
        self.assertEqual(self.commandlog.read_text().count('android.ready 1'), 1)

    def test_full_guarded_budget_and_actual_180_second_check(self):
        self.timed_fixture()
        r = self.timed_run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + ((self.d / 'fault').read_text() if (self.d / 'fault').exists() else ''))
        marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')}
        arm = marks['guards-arm']
        self.assertEqual(marks['offline-audit-end'] - marks['offline-audit-begin'], 157)
        self.assertEqual(marks['start-attempt'] - arm, 3)
        self.assertGreaterEqual(marks['stop-request'] - marks['running'], 180)
        self.assertLess(marks['stop-evidence-complete'] - arm, 235)
        self.assertTrue((self.d / 'guardian.pid').exists())
        self.assertTrue((self.d / 'watchdog.pid').exists())
        self.assertTrue((self.d / 'complete').exists())
        self.assertTrue((self.d / 'stores-post-complete').exists())
        self.assertEqual(self.state.read_text().strip(), 'offline')
        self.assertEqual(len((self.d / 'kills').read_text().splitlines()), 9)
        print('full production stages + live guards, virtual phase seconds:',
              {k: v-arm for k,v in marks.items()}, flush=True)

    def test_review58_slow_post_store_cannot_complete_after_later_admission(self):
        self.timed_fixture(gate=6, diag=2, post_store=40)
        r = self.timed_run()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.d / 'start-attempted').exists())
        self.assertFalse((self.d / 'complete').exists())
        self.assertTrue((self.d / 'fault').exists())
        self.assertEqual(self.state.read_text().strip(), 'offline')

    def test_cleanup_calibration_refuses_slow_audit_or_store_before_guards(self):
        for costs in ({'pre_store': 13}, {'pre_audit': 12}):
            with self.subTest(costs=costs):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(**costs)
                    r = f.timed_run()
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertIn('measured cleanup', r.stderr)
                    self.assertFalse((f.d / 'guard-armed.uptime').exists())
                    self.assertFalse((f.d / 'start-attempted').exists())
                    self.assertTrue((f.d / 'abort').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertEqual(len((f.d / 'kills').read_text().splitlines()), 9)
                finally:
                    f.doCleanups()

    def test_phone_calibrations_fit_fixed_reserve(self):
        measurements = json.loads((Path(__file__).with_name('phone-budget-timings.json')).read_text())
        for trial in measurements['trials']:
            with self.subTest(trial=trial['trial']):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    # Reproduce the stored rounded calibration exactly. POST
                    # uses the conservative phone cost bounds (not zero), and
                    # six seconds of kills, slower than measured helper stop.
                    audit, store = trial['audit_cost'], trial['store_cost']
                    f.timed_fixture(proof=int(trial['offline_proof_seconds']) - (audit - 1),
                                    pre_audit=audit - 1, pre_store=store - 1,
                                    post_audit=audit, post_store=store, stop=1)
                    r = f.timed_run()
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    tail = trial['cleanup_estimate']
                    stop = min(20, 35 - tail - 1)
                    self.assertIn(f'cleanup_estimate={tail} tail_limit={tail} stop_limit={stop} handoff=1 total_limit=35', r.stdout)
                    self.assertEqual(int((f.d / 'cleanup-budget-verified').read_text()), tail)
                    self.assertTrue((f.d / 'start-attempted').exists())
                    self.assertTrue((f.d / 'stores-post-complete').exists())
                    self.assertTrue((f.d / 'complete').exists())
                    marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines()
                             if l.startswith('A9_WIFI_TIMING ')}
                    arm = marks['guards-arm']
                    self.assertGreaterEqual(marks['stop-request'] - marks['running'], 180)
                    self.assertLess(marks['stop-evidence-complete'] - marks['stop-request'], 35)
                    self.assertLess(marks['stop-evidence-complete'] - arm, 235)
                    self.assertLess(marks['modem-offline'], marks['helpers-stopped'])
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    print('PHONE calibration replay trial', trial['trial'],
                          {k: v-arm for k,v in marks.items()}, flush=True)
                finally:
                    f.doCleanups()

    def test_cleanup_allocation_never_increases_total_or_stop_bound(self):
        # Execute production arithmetic for every admitted integer tail, plus
        # malformed/boundary records. No test copy of its allocation formula.
        functions = self.script.read_text().split('case "${1:-}" in', 1)[0]
        r = self.function_probe('''
for cost in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33; do
    echo "$cost" > "$D/cleanup-budget-verified"
    cleanup_limits || exit 2
    [ "$tail_limit" = "$cost" ] && [ "$stop_limit" -gt 0 ] && [ "$stop_limit" -le 20 ] || exit 3
    [ "$((tail_limit + stop_limit + 1))" -le 35 ] || exit 4
done
for cost in 0 34 35 bad '16 extra'; do
    echo "$cost" > "$D/cleanup-budget-verified"
    if cleanup_limits; then exit 5; fi
done
''', functions)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_phone_trials_3_4_5_whole_budget_reaches_start_and_completes(self):
        measurements = json.loads((Path(__file__).with_name('phone-budget-timings.json')).read_text())
        self.assertEqual([t['trial'] for t in measurements['trials']], [3, 4, 5])
        for trial in measurements['trials']:
            with self.subTest(trial=trial['trial']):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    audit, store = trial['audit_cost'], trial['store_cost']
                    # Full measured offline proof is outside the armed window.
                    # Guard costs include launch, BOTH gates, DIAG and admission
                    # overhead. Missing old phases use explicit stress profiles
                    # in the fixture, never the former 1+1+1 second assumption.
                    stop = min(19, 35 - trial['cleanup_estimate'] - 1)
                    measured = trial['timings']
                    offline_costs = dict(proof=trial['offline_proof_seconds'] - (audit - 1),
                                         pre_audit=audit - 1, pre_store=store - 1)
                    gate_costs = trial['guarded_replay'].copy()
                    if trial['trial'] == 3:
                        offline_costs['arm_gap'] = round(measured['guards-arm'] - measured['offline-audit-end'] - (store - 1), 3)
                    elif trial['trial'] == 4:
                        # No arm exists in this refused trial. Conservatively
                        # charge its entire offline-end -> stop-request gap to
                        # pre-arm work rather than inventing a gate measurement.
                        offline_costs['post_proof_gap'] = round(measured['stop-request'] - measured['offline-audit-end'] - (store - 1), 3)
                    if trial['trial'] == 5:
                        interval = lambda end, begin: round(measured[end] - measured[begin], 3)
                        offline_costs.update(proof=interval('calibration-audit-begin', 'offline-audit-begin'),
                                             pre_audit=interval('calibration-audit-end', 'calibration-audit-begin'),
                                             post_audit_gap=interval('offline-audit-end', 'calibration-audit-end'),
                                             post_proof_gap=interval('stores-pre-begin', 'offline-audit-end'),
                                             pre_store=interval('stores-pre-end', 'stores-pre-begin'),
                                             arm_gap=interval('guards-arm', 'stores-pre-end'))
                        gate_costs.update(diag_gap=interval('start-diag-begin', 'start-gate-before-end'),
                                          after_gate_gap=interval('start-gate-after-begin', 'start-diag-end'),
                                          freshness_gap=interval('start-freshness-end', 'start-gate-after-end'))
                    f.timed_fixture(**offline_costs,
                                    post_audit=audit, post_store=store, stop=stop,
                                    startup=9, final=4, **gate_costs)
                    (f.root / 'uptime').write_text(f"{measured['offline-audit-begin']:.3f} 0.0\n")
                    r = f.timed_run(timeout=60)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue((f.d / 'start-write-returned').exists())
                    self.assertTrue((f.d / 'stores-post-complete').exists())
                    self.assertTrue((f.d / 'complete').exists())
                    self.assertEqual(int((f.d / 'cleanup-budget-verified').read_text()), trial['cleanup_estimate'])
                    marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines()
                             if l.startswith('A9_WIFI_TIMING ')}
                    arm = int((f.d / 'guard-armed.uptime').read_text())
                    if trial['trial'] == 5:
                        for label in ('offline-audit-begin', 'calibration-audit-begin', 'calibration-audit-end',
                                      'offline-audit-end', 'stores-pre-begin', 'stores-pre-end', 'guards-arm',
                                      'start-gate-before-begin', 'start-gate-before-end', 'start-diag-begin',
                                      'start-diag-end', 'start-gate-after-begin', 'start-gate-after-end', 'start-freshness-end'):
                            self.assertAlmostEqual(marks[label], measured[label], places=2, msg=label)
                    self.assertAlmostEqual(marks['start-gate-before-end'] - marks['start-gate-before-begin'], trial['guarded_replay']['gate'])
                    self.assertAlmostEqual(marks['start-gate-after-end'] - marks['start-gate-after-begin'], trial['guarded_replay']['gate_after'])
                    self.assertGreaterEqual(marks['stop-request'] - marks['running'], 180)
                    self.assertLess(marks['running'] - arm, 29)
                    self.assertLess(marks['modem-offline'] - arm, 235)
                    self.assertLess(marks['stop-evidence-complete'] - arm, 265)
                    self.assertLess(marks['stop-evidence-complete'] - marks['stop-request'], 35)
                    self.assertLess(marks['modem-offline'], marks['helpers-stop-begin'])
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    print('WHOLE PHONE replay trial', trial['trial'],
                          {k: round(v-arm, 3) for k,v in marks.items()},
                          'margins', {'offline': round(235-(marks['modem-offline']-arm), 3),
                                      'evidence': round(265-(marks['stop-evidence-complete']-arm), 3)}, flush=True)
                finally:
                    f.doCleanups()

    def test_derived_activation_deadline_and_admission_boundary(self):
        self.start_fixture()
        self.env['FIXTURE_CLOCK'] = str(self.root / 'uptime')
        functions = self.script.read_text().split('case "${1:-}" in', 1)[0]
        r = self.function_probe('''
[ "$(active_end)" = 215 ] && [ "$(activation_end)" = 29 ] || exit 2
echo '18.999 0.0' > "$FIXTURE_CLOCK"
start_budget || exit 3
echo '19.000 0.0' > "$FIXTURE_CLOCK"
if start_budget; then exit 4; fi
''', functions)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_offline_evidence_can_cross_240_without_disarming_270_guard(self):
        for finish in (True, False):
            with self.subTest(finish=finish):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0, gate=8, diag=1, admission=1,
                                    startup=9, final=4, stop=19)
                    f.add_hooks('''
if [ "$model_stage" = --stop-body ]; then echo "$$" > "$D/stop-body-pid"; fi
if [ "$model_stage" = stop ]; then echo "$$" > "$D/stop-owner-pid"; fi
stores() {
    verified_stores "$1" || return 1
    if [ "$1" = post ]; then
        echo ready > "$D/evidence-held"
        while [ ! -e "$D/release-evidence" ]; do command sleep 0.01; done
    else advance "$COST_PRE_STORE"; fi
}
''')
                    p = f.launch_timed_run()
                    f.wait_fixture(f.d / 'evidence-held')
                    arm = int((f.d / 'guard-armed.uptime').read_text())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    # Tail begins at arm+231, deadline arm+241. At 240 the
                    # independent stop child still runs, with no false fault.
                    f.set_fixture_clock(arm + 240)
                    f.wait_fixture(f.d / 'guardian-stop.pid')
                    guardian_stop = int((f.d / 'guardian-stop.pid').read_text())
                    end = time.monotonic() + 10 * TIMEOUT_SCALE
                    while time.monotonic() < end:
                        stat = Path(f'/proc/{guardian_stop}/stat')
                        try:
                            if not stat.exists() or stat.read_text().rsplit(') ', 1)[1].split()[0] == 'Z': break
                        except (FileNotFoundError, ProcessLookupError):
                            break  # the process vanished between the check and the read
                        time.sleep(.01)
                    self.assertFalse((f.d / 'fault').exists())
                    self.assertFalse((f.d / 'complete').exists())
                    if finish:
                        (f.d / 'release-evidence').touch()
                        p.wait(timeout=15 * TIMEOUT_SCALE)
                        self.assertEqual(p.returncode, 0, (f.root / 'run.stderr').read_text())
                        self.assertTrue((f.d / 'complete').exists())
                    else:
                        # Kill controller + its bounded work: only guardian
                        # remains responsible for incomplete evidence at 270.
                        p.kill(); p.wait(timeout=5)
                        os.kill(int((f.d / 'stop-owner-pid').read_text()), 9)
                        os.killpg(int((f.d / 'stop-body-pid').read_text()), 9)
                        f.set_fixture_clock(arm + 269)
                        time.sleep(.05)
                        self.assertFalse(f.commandlog.exists() and 'reboot,bootloader' in f.commandlog.read_text())
                        f.set_fixture_clock(arm + 270)
                        end = time.monotonic() + 10 * TIMEOUT_SCALE
                        while (not f.commandlog.exists() or 'reboot,bootloader' not in f.commandlog.read_text()) and time.monotonic() < end:
                            time.sleep(.01)
                        self.assertIn('reboot,bootloader', f.commandlog.read_text())
                        self.assertFalse((f.d / 'complete').exists())
                finally:
                    (f.d / 'release-evidence').touch()
                    f.doCleanups()

    def test_stop_phase_cannot_borrow_evidence_window_before_offline(self):
        self.timed_fixture(proof=0)
        self.add_hooks('''
setprop() {
    if [ "$model_stage" = --stop-modem ]; then advance 20; fi
}
''')
        r = self.timed_run()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.d / 'complete').exists())
        self.assertFalse((self.d / 'kills').exists())
        self.assertIn('reboot,bootloader', self.commandlog.read_text())

    def test_late_evidence_is_capped_at_265_not_270(self):
        self.timed_fixture(proof=0)
        self.state.write_text('offline\n')
        (self.d / 'guard-armed.uptime').write_text('0\n')
        (self.d / 'cleanup-budget-verified').write_text('33\n')
        self.set_fixture_clock(233)
        self.add_hooks('''
audit() {
    echo entered > "$D/late-audit"
    while [ ! -e "$D/release-late-audit" ]; do command sleep 0.01; done
    verified_audit
}
''')
        p = subprocess.Popen([shutil.which('sh'), str(self.script), 'stop'], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.wait_fixture(self.d / 'late-audit')
        try:
            self.set_fixture_clock(264)
            time.sleep(.05)
            self.assertIsNone(p.poll())
            self.assertFalse((self.d / 'complete').exists())
            self.set_fixture_clock(265)
            p.wait(timeout=15 * TIMEOUT_SCALE)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse((self.d / 'complete').exists())
            self.assertFalse((self.d / 'kills').exists())
            self.assertIn('reboot,bootloader', self.commandlog.read_text())
        finally:
            (self.d / 'release-late-audit').touch()

    def test_trial4_tail_is_bounded_and_cannot_borrow_stop_or_absolute_slack(self):
        # Calibrates 16 s, then tests below and at its enforced tail bound.
        for post_store, succeeds in ((7, True), (8, False)):
            with self.subTest(post_store=post_store):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0, pre_audit=1, pre_store=2,
                                    post_audit=2, post_store=post_store, stop=18,
                                    startup=9, final=4)
                    r = f.timed_run()
                    self.assertEqual(r.returncode == 0, succeeds, r.stdout + r.stderr)
                    self.assertEqual((f.d / 'complete').exists(), succeeds)
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    if succeeds:
                        marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines()
                                 if l.startswith('A9_WIFI_TIMING ')}
                        self.assertEqual(marks['stop-evidence-complete'] - marks['stop-request'], 33)
                        self.assertLess(marks['stop-evidence-complete'] - marks['guards-arm'], 235)
                    else:
                        self.assertIn('reboot,bootloader', f.commandlog.read_text())
                finally:
                    f.doCleanups()

    def test_trial4_shorter_modem_stop_timeout_retains_helpers_on_failure(self):
        self.timed_fixture(proof=0, pre_audit=1, pre_store=2)
        self.add_hooks('''
timeout() {
    case "$*" in *' --stop-state')
        echo "$4" > "$D/observed-stop-limit"
        return 124;;
    esac
    command timeout "$@"
}
''')
        # Keep MPSS running so stop_modem must invoke its actual timeout path.
        self.stuck = True
        r = self.timed_run()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.d / 'observed-stop-limit').read_text().strip(), '18')
        self.assertFalse((self.d / 'complete').exists())
        self.assertFalse((self.d / 'kills').exists())
        self.assertIn('reboot,bootloader', self.commandlog.read_text())

    def test_slow_startup_and_final_checks_fail_with_cleanup_reserved(self):
        for costs in ({'startup': 11}, {'final': 6}):
            with self.subTest(costs=costs):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(**costs)
                    r = f.timed_run()
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue((f.d / 'start-attempted').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    # Advancing the synthetic clock inside the writer races
                    # its returned marker against the timeout supervisor. An
                    # uncertain write MUST retain helpers and request reset;
                    # a returned write permits ordered bounded completion.
                    uncertain = not (f.d / 'start-write-returned').exists()
                    if uncertain:
                        self.assertIn('startup', costs)
                        self.assertFalse((f.d / 'complete').exists())
                        self.assertFalse((f.d / 'kills').exists())
                        self.assertIn('reboot,bootloader', f.commandlog.read_text())
                    else:
                        self.assertTrue((f.d / 'complete').exists(), r.stdout + r.stderr)
                    marks = {l.split()[1]: float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith('A9_WIFI_TIMING ')}
                    self.assertLess(marks['stop-request'] - marks['guards-arm'], 200)
                    if not uncertain:
                        self.assertLess(marks['stop-evidence-complete'] - marks['guards-arm'], 235)
                finally:
                    f.doCleanups()

    def test_slow_post_audit_or_store_never_publishes_successful_completion(self):
        for costs in ({'post_store': 40}, {'post_audit': 16}):
            with self.subTest(costs=costs):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(**costs)
                    r = f.timed_run()
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertFalse((f.d / 'complete').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertIn('reboot,bootloader', f.commandlog.read_text())
                finally:
                    f.doCleanups()

    def test_run_failure_or_signal_cancels_before_preflight_and_during_monitor_launch(self):
        faults = {
            'observe': 'observe() { die injected-observe-failure; }',
            'audit': 'audit() { return 1; }',
            'store-mount': 'mount() { return 1; }',
            'store-copy': 'stores() { return 1; }',
            'preflight-signal': 'observe() { command kill -TERM "$(cat "$D/fixture-owner")"; return 1; }',
            'store-signal': 'stores() { command kill -TERM "$(cat "$D/fixture-owner")"; return 1; }',
            'launch-signal': 'nohup() { command kill -TERM "$(cat "$D/fixture-owner")"; command nohup "$@"; }',
        }
        for fault, hook in faults.items():
            with self.subTest(fault=fault):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0)
                    text = f.script.read_text().replace('case "${1:-}" in',
                        'if [ "$model_stage" = run ]; then echo "$$" > "$D/fixture-owner"; fi\n' + hook + '\ncase "${1:-}" in', 1)
                    f.script.write_text(text)
                    r = f.timed_run()
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertFalse((f.d / 'start-attempted').exists())
                    self.assertTrue((f.d / 'fault').exists())
                    self.assertTrue((f.d / 'abort').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertEqual(len((f.d / 'kills').read_text().splitlines()), 9, r.stdout + r.stderr)
                    # Published cancellation remains terminal even with ready
                    # files left by a partly launched monitor.
                    retry = f.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
                    self.assertNotEqual(retry.returncode, 0)
                    self.assertFalse((f.d / 'start-attempted').exists())
                finally:
                    f.doCleanups()

    def wait_fixture(self, path, timeout=30):
        end = time.monotonic() + timeout * TIMEOUT_SCALE
        while not path.exists() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(path.exists(), 'missing fixture event: ' + str(path))

    def launch_timed_run(self):
        out = (self.root / 'run.stdout').open('w')
        err = (self.root / 'run.stderr').open('w')
        p = subprocess.Popen([shutil.which('sh'), str(self.script), 'run',
                              'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1'*64],
                             env=self.env, stdout=out, stderr=err)
        self.addCleanup(out.close); self.addCleanup(err.close)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def set_fixture_clock(self, value):
        tmp = self.root / 'clock-driver'
        tmp.write_text(f'{value}.0 0.0\n')
        tmp.replace(self.root / 'uptime')

    def add_hooks(self, hooks):
        self.script.write_text(self.script.read_text().replace('case "${1:-}" in', hooks + '\ncase "${1:-}" in', 1))

    def test_hung_active_check_is_stopped_at_absolute_cleanup_cutoff(self):
        self.timed_fixture(proof=0)
        self.add_hooks('''
kernel_evidence() {
    echo entered > "$D/hung-check"
    while :; do command sleep 0.01; done
}
''')
        p = self.launch_timed_run()
        self.wait_fixture(self.d / 'hung-check')
        arm = int((self.d / 'guard-armed.uptime').read_text())
        self.set_fixture_clock(arm + 215)
        p.wait(timeout=15 * TIMEOUT_SCALE)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((self.d / 'complete').exists())
        self.assertEqual(self.state.read_text().strip(), 'offline')
        marks = {l.split()[1]: float(l.split()[2]) for l in (self.root / 'run.stdout').read_text().splitlines()
                 if l.startswith('A9_WIFI_TIMING ')}
        self.assertEqual(marks['stop-request'] - arm, 215)
        self.assertLess(marks['modem-offline'] - arm, 235)
        self.assertLess(marks['stop-evidence-complete'] - arm, 265)

    def test_immediate_rmtfs_failure_and_watchdog_loss_stop_before_helpers(self):
        for fault in ('rmtfs', 'watchdog'):
            with self.subTest(fault=fault):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0)
                    action = ('echo "A9_WIFI_FAULT rmtfs synthetic" >> "$D/rmtfs.log"' if fault == 'rmtfs'
                              else 'command kill -KILL "$(cat "$D/watchdog.pid")"')
                    f.add_hooks('''
services() {
    if [ "$model_stage" = --post-start ]; then ACTION; fi
    verified_services
}
'''.replace('ACTION', action))
                    r = f.timed_run()
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue((f.d / 'start-attempted').exists())
                    self.assertTrue((f.d / 'complete').exists(), r.stdout + r.stderr)
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertEqual(len((f.d / 'kills').read_text().splitlines()), 9)
                finally:
                    f.doCleanups()

    def test_hung_post_stop_audit_and_copy_cannot_mark_complete(self):
        for op in ('audit', 'stores'):
            with self.subTest(op=op):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0)
                    f.add_hooks(op + '''() {
    if [ "$model_stage" = --stop-tail ]; then
        echo entered > "$D/hung-tail"
        while :; do command sleep 0.01; done
    fi
    verified_''' + op + ''' "$@"
}
''')
                    p = f.launch_timed_run()
                    f.wait_fixture(f.d / 'hung-tail')
                    now = int(float((f.root / 'uptime').read_text().split()[0]))
                    f.set_fixture_clock(now + int((f.d / 'cleanup-budget-verified').read_text()))
                    p.wait(timeout=15 * TIMEOUT_SCALE)
                    self.assertNotEqual(p.returncode, 0)
                    self.assertFalse((f.d / 'complete').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertIn('reboot,bootloader', f.commandlog.read_text())
                finally:
                    f.doCleanups()

    def test_controller_loss_preserves_real_240_stop_and_270_reset_with_blocked_stop(self):
        self.timed_fixture(proof=0)
        self.add_hooks('''
kernel_evidence() {
    echo entered > "$D/loss-ready"
    while :; do command sleep 0.01; done
}
stop_modem() {
    if [ "$model_stage" = --guardian-stop ]; then
        echo entered > "$D/blocked-orderly-stop"
        while [ ! -e "$D/release-stop" ]; do command sleep 0.01; done
    fi
    verified_stop_modem
}
''')
        p = self.launch_timed_run()
        self.wait_fixture(self.d / 'loss-ready')
        arm = int((self.d / 'guard-armed.uptime').read_text())
        p.kill(); p.wait(timeout=5)
        watchdog = int((self.d / 'watchdog.pid').read_text())
        os.kill(watchdog, 9)  # guardian must work even with BOTH owners gone
        self.set_fixture_clock(arm + 239)
        time.sleep(.05)
        self.assertFalse((self.d / 'guardian-stop.pid').exists())
        self.set_fixture_clock(arm + 240)
        self.wait_fixture(self.d / 'blocked-orderly-stop')
        self.assertFalse(self.commandlog.exists() and 'reboot,bootloader' in self.commandlog.read_text())
        self.set_fixture_clock(arm + 269)
        time.sleep(.05)
        self.assertFalse(self.commandlog.exists() and 'reboot,bootloader' in self.commandlog.read_text())
        self.set_fixture_clock(arm + 270)
        end = time.monotonic() + 10 * TIMEOUT_SCALE
        while (not self.commandlog.exists() or 'reboot,bootloader' not in self.commandlog.read_text()) and time.monotonic() < end:
            time.sleep(.01)
        self.assertIn('reboot,bootloader', self.commandlog.read_text())
        self.assertFalse((self.d / 'complete').exists())
        self.assertFalse((self.d / 'kills').exists())  # no helper teardown while MPSS running
        (self.d / 'release-stop').touch()
        self.wait_fixture(self.d / 'watchdog-ready')

    def test_timeout_kills_descendants_before_they_can_write_late(self):
        # Run actual GNU / Android Toybox timeout, not the virtual-clock shim.
        # Its default process-group kill is required by nested bounded stages.
        child = self.root / 'nested.sh'
        child.write_text('''timeout --foreground -s KILL 60 sh -c 'echo $$ > "$1"; sleep 30; echo unsafe > "$2"' sh "$1" "$2" &
wait
''')
        pidfile, late = self.root / 'descendant.pid', self.root / 'late'
        r = subprocess.run([shutil.which('timeout'), '-s', 'KILL', str(.5 * TIMEOUT_SCALE),
                            shutil.which('sh'), str(child), str(pidfile), str(late)],
                           env=self.env, capture_output=True, timeout=10 * TIMEOUT_SCALE)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(pidfile.exists())
        pid = int(pidfile.read_text())
        stat = Path(f'/proc/{pid}/stat')
        if stat.exists():
            self.assertEqual(stat.read_text().rsplit(') ', 1)[1].split()[0], 'Z')
        self.assertFalse(late.exists())

    def test_failed_or_partial_guard_launch_cancels_and_tears_down_offline(self):
        for monitor in ('guardian', 'watchdog'):
            with self.subTest(monitor=monitor):
                f = Controller(methodName='runTest'); f.setUp()
                try:
                    f.timed_fixture(proof=0)
                    launcher = f.bin / 'nohup'
                    launcher.write_text('#!' + shutil.which('python3') + '\n' +
                        'import os, sys\n' +
                        'if sys.argv[-1] == ' + repr('--' + monitor) + ': sys.exit(1)\n' +
                        'os.execv(' + repr(shutil.which('nohup')) + ', [\'nohup\']+sys.argv[1:])\n')
                    launcher.chmod(0o700)
                    p = f.launch_timed_run()
                    f.wait_fixture(f.d / 'guard-armed.uptime')
                    other = 'watchdog' if monitor == 'guardian' else 'guardian'
                    f.wait_fixture(f.d / (other + '-ready'))
                    arm = int((f.d / 'guard-armed.uptime').read_text())
                    f.set_fixture_clock(arm + 5)
                    p.wait(timeout=15 * TIMEOUT_SCALE)
                    self.assertNotEqual(p.returncode, 0)
                    self.assertFalse((f.d / 'start-attempted').exists())
                    self.assertTrue((f.d / 'abort').exists())
                    self.assertTrue((f.d / 'complete').exists())
                    self.assertEqual(f.state.read_text().strip(), 'offline')
                    self.assertEqual(len((f.d / 'kills').read_text().splitlines()), 9)
                finally:
                    f.doCleanups()

    def test_timed_out_start_writer_cannot_authorize_helper_teardown(self):
        self.timed_fixture(proof=0)
        self.script.write_text(self.script.read_text().replace(
            "'read n idle <", "'echo entered > \"$2/hung-writer\"; while :; do sleep 1; done; read n idle <"))
        p = self.launch_timed_run()
        self.wait_fixture(self.d / 'hung-writer')
        arm = int((self.d / 'guard-armed.uptime').read_text())
        self.set_fixture_clock(arm + 15)
        p.wait(timeout=15 * TIMEOUT_SCALE)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((self.d / 'start-write-pending').exists())
        self.assertFalse((self.d / 'start-write-returned').exists())
        self.assertFalse((self.d / 'kills').exists())
        self.assertFalse((self.d / 'complete').exists())
        self.assertIn('reboot,bootloader', self.commandlog.read_text())

    def identity_log(self):
        return ('[ 1.234] ath10k_snoc c800000.wifi: qmi chip_id 0x01 chip_family 0x0 board_id 0x002 soc_id 0x0\n'
                '[ 1.235] ath10k_snoc c800000.wifi: qmi fw_version 0xABC fw_build_timestamp 2026-01-02 03:04 fw_build_id TEST.FW.1\n')

    def identity_probe(self, text, tail='capture_identity\n'):
        (self.d / 'kernel-identity.txt').write_text(text)
        fixture = self.root / 'kernel-input'
        fixture.write_text(text)
        self.env['FIXTURE_KMSG'] = str(fixture)
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'identity-probe.sh'
        hooks = '''
monitors() { return 0; }
timeout() {
    if [ "$1" = --foreground ]; then shift; fi
    if [ "$1" = -s ]; then shift 2; fi
    shift
    if [ "$1" = dmesg ]; then cat "$FIXTURE_KMSG"; return; fi
    printf "%s\\n" "$*" >> "$D/scan-commands"
    case "$*" in *' dev wlan0 scan') echo 'BSS fixture';; esac
}
'''
        probe.write_text(functions + hooks + '\n' + tail)
        return subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)

    def test_identity_canonicalizes_fields_and_omits_printk_timestamps(self):
        text = self.identity_log()
        result = self.identity_probe(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = (self.d / 'identity.sha256').read_text()
        canonical = (self.d / 'identity.txt').read_text()
        self.assertIn('device=ath10k_snoc c800000.wifi\n', canonical)
        self.assertIn('chip_id=0x1\nchip_family=0x0\nboard_id=0x2\nsoc_id=0x0\n', canonical)
        self.assertNotIn('[ 1.234]', canonical)
        # Same actual metadata, different printk times/newline presentation.
        text = text.replace('[ 1.234]', '[ 900.001]').replace('[ 1.235]', '[ 900.002]').replace('0xABC', '0x0abc').replace('\n[ 900.002]', ' [ 900.002]')
        result = self.identity_probe(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.d / 'identity.sha256').read_text(), first)

    def test_missing_ambiguous_default_or_malformed_identity_is_rejected(self):
        good = self.identity_log()
        for text in (good.splitlines()[0] + '\n', good + good.splitlines()[0] + '\n',
                     good.replace('chip_id 0x01', 'chip_id 0x000000ff'),
                     good.replace('board_id 0x002', 'board_id 0xff'),
                     good.replace('chip_family 0x0', 'chip_family xyz'),
                     good.replace('fw_build_id TEST.FW.1', 'fw_build_id '),
                     good.replace('TEST.FW.1', 'X' * 129),
                     good.replace('[ 1.235] ath10k_snoc c800000.wifi:', '[ 1.235] ath10k_snoc different.wifi:')):
            with self.subTest(text=text):
                (self.d / 'identity.sha256').unlink(missing_ok=True)
                result = self.identity_probe(text)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.d / 'identity.sha256').exists())

    def test_capability_only_and_mismatched_digest_cannot_run_scan_commands(self):
        result = self.identity_probe(self.identity_log(), 'capture_identity\nscan_if_reviewed\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.d / 'scan-commands').exists())
        self.assertFalse((self.d / 'scan-attempted').exists())
        (self.d / 'scan-reviewed.sha256').write_text('0' * 64 + '\n')
        result = self.identity_probe(self.identity_log(), 'capture_identity\nscan_if_reviewed\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.d / 'scan-commands').exists())
        self.assertFalse((self.d / 'scan-attempted').exists())

    def test_matching_reviewed_identity_permits_exactly_one_bounded_scan(self):
        result = self.identity_probe(self.identity_log(), 'capture_identity\ncp "$D/identity.sha256" "$D/scan-reviewed.sha256"\nscan_if_reviewed\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.d / 'scan-completed').exists())
        commands = (self.d / 'scan-commands').read_text()
        self.assertIn('ip link set wlan0 up', commands)
        self.assertEqual(commands.count('dev wlan0 scan'), 1)
        result = self.identity_probe(self.identity_log(), 'scan_if_reviewed\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.d / 'scan-commands').read_text(), commands)

    def test_kernel_fault_before_scan_prevents_linkup_and_scan(self):
        for error in ('capability req rejected: 1', 'found invalid board magic', 'could not init core (-1)'):
            with self.subTest(error=error):
                text = self.identity_log() + 'ath10k_snoc c800000.wifi: ' + error + '\n'
                result = self.identity_probe(text, 'capture_identity\ncp "$D/identity.sha256" "$D/scan-reviewed.sha256"\nscan_if_reviewed\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((self.d / 'fault').exists())
                self.assertFalse((self.d / 'scan-commands').exists())
                self.assertFalse((self.d / 'scan-attempted').exists())

    def test_android_without_reviewed_scan_is_rejected_before_start(self):
        result = self.run_stage('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1' * 64, '--android')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--android requires --scan-reviewed', result.stderr)
        self.assertFalse((self.d / 'start-attempted').exists())
        self.assertFalse((self.d / 'stores').exists())
        (self.d / 'stage').write_text('checked\n')
        result = self.run_stage('android')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('this trial reviewed scan completion', result.stderr)

    def test_standalone_check_cannot_supply_scan_eligibility(self):
        result = self.run_stage('check', '--scan-reviewed', '1' * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('check accepts no scan eligibility argument', result.stderr)
        self.assertFalse((self.d / 'scan-reviewed.sha256').exists())
        self.assertFalse((self.d / 'scan-attempted').exists())

    def test_stale_android_identity_cannot_enable_framework(self):
        (self.d / 'stage').write_text('checked\n')
        (self.d / 'scan-completed').touch()
        (self.d / 'scan-reviewed.sha256').write_text('1' * 64 + '\n')
        (self.d / 'identity.sha256').write_text('2' * 64 + '\n')
        (self.d / 'guard-armed.uptime').write_text('0\n')
        result = self.run_stage('android')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('this trial reviewed scan identity', result.stderr)
        self.assertFalse(self.commandlog.exists())

    def test_stale_monitor_ready_files_do_not_authorize_start(self):
        self.state.write_text('offline\n')
        for name in ('watchdog-ready', 'guardian-ready'):
            (self.d / name).touch()
        for name in ('watchdog.pid', 'guardian.pid'):
            (self.d / name).write_text('99999999\n')
        (self.d / 'stage').write_text('prepared\n')
        result = self.run_stage('--start', 'I_AUTHORIZE_ONE_MPSS_START')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        self.assertFalse((self.d / 'start-attempted').exists())

    def test_diag_lookup_actual_pinned_numeric_instance_format(self):
        tool = self.bin / 'qrtr-lookup-fixture'
        tool.write_text('#!' + shutil.which('sh') + '\ncat "$FIXTURE_LOOKUP_OUTPUT"\n')
        tool.chmod(0o700)
        output = self.root / 'qrtr-output'
        self.env['FIXTURE_LOOKUP_OUTPUT'] = str(output)
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'lookup-test.sh'
        probe.write_text(functions.replace('/vendor/bin/qrtr-lookup', str(tool)) + '\ndiag_lookup\n')
        (self.d / 'diag-router.log').write_text('A9_WIFI_LOCAL_NODE 7\nA9_WIFI_READY diag-router\n')
        good = '  Service Version Instance Node  Port\n4097 N/A 0 7 12289 DIAG service (MODEM:CNTL)\n4097 N/A 2 7 12290 DIAG service (MODEM:DATA)\n4097 N/A 4 7 12291 DIAG service (MODEM:DCI)\n'
        output.write_text(good)
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output.write_text(good.replace('N/A 4 7', 'N/A 1 7'))
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_fault_death_denial_short_io_stall_and_abort_stop_modem(self):
        for fault in ('companion-death', 'parser-denial', 'short-io', 'stalled-tftp', 'operator-abort'):
            with self.subTest(fault=fault):
                for name in ('fault', 'abort', 'watchdog-stopped', 'rmtfs.exit'):
                    (self.d / name).unlink(missing_ok=True)
                self.state.write_text('running\n')
                (self.d / 'rmtfs.log').write_text('A9_WIFI_READY rmtfs\n')
                if fault == 'companion-death':
                    (self.d / 'rmtfs.exit').write_text('1')
                elif fault == 'operator-abort':
                    (self.d / 'abort').touch()
                else:
                    (self.d / 'rmtfs.log').write_text('A9_WIFI_FAULT ' + fault + '\n')
                result = self.run_stage('--watchdog')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_text().strip(), 'offline', result.stderr)
                self.assertTrue((self.d / 'watchdog-stopped').exists())
                self.assertFalse((self.d / 'complete').exists())  # surviving RAM untouched

    def test_nominal_companion_teardown_retires_only_after_proven_offline(self):
        self.state.write_text('offline\n')
        (self.d / 'stop-requested').touch()
        (self.d / 'start-attempted').touch()
        (self.d / 'started.uptime').write_text('0\n')
        (self.d / 'rmtfs.exit').write_text('143\n')
        (self.d / 'rmtfs.tracer').unlink()  # intentional tracer teardown too
        (self.d / 'guardian-ready').touch()
        result = self.run_stage('--watchdog')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.d / 'watchdog-retired').exists())
        self.assertFalse((self.d / 'fault').exists())
        self.assertFalse((self.d / 'complete').exists())  # guardian not disarmed
        self.assertTrue((self.d / 'guardian-ready').exists())

    def test_requested_stop_still_faults_on_companion_death_before_offline(self):
        self.state.write_text('running\n')
        (self.d / 'stop-requested').touch()
        (self.d / 'start-attempted').touch()
        (self.d / 'started.uptime').write_text('0\n')
        (self.d / 'rmtfs.exit').write_text('1\n')
        result = self.run_stage('--watchdog')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        self.assertIn('companion-death', (self.d / 'fault').read_text())
        self.assertTrue((self.d / 'watchdog-stopped').exists())
        self.assertFalse((self.d / 'watchdog-retired').exists())

    def test_watchdog_still_stops_when_fault_log_is_unwritable(self):
        self.state.write_text('running\n')
        (self.d / 'fault').mkdir()
        result = self.run_stage('--watchdog')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'offline', result.stderr)
        self.assertTrue((self.d / 'watchdog-stopped').exists())

    def test_abort_marker_failure_stops_modem_directly(self):
        self.state.write_text('running\n')
        (self.d / 'abort').mkdir()
        (self.d / 'fault').mkdir()  # both evidence writes fail
        result = self.run_stage('abort')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MPSS stopped directly', result.stderr)
        self.assertEqual(self.state.read_text().strip(), 'offline', result.stderr)
        self.assertFalse((self.d / 'watchdog-ready').exists())

    def test_failed_android_stop_property_still_stops_and_preserves_failure(self):
        original = self.script.read_text()
        # No real companion processes in this fixture. Audit success isolates
        # the property-service failure; inspect the retained rc after teardown.
        hooks = '\nROLES=""\naudit() { return 0; }\nsetprop() { return 1; }\n'
        fixture = original.replace('case "${1:-}" in', hooks + 'case "${1:-}" in', 1)
        fixture = fixture.replace('echo complete > "$D/complete"',
                                  'echo "$rc" > "$D/fixture-final-rc"\n        echo complete > "$D/complete"', 1)
        self.script.write_text(fixture)
        for logging in ('writable', 'unwritable'):
            with self.subTest(logging=logging):
                self.state.write_text('running\n')
                fault = self.d / 'fault'
                if fault.exists():
                    fault.unlink()
                if logging == 'unwritable':
                    fault.mkdir()
                result = self.run_stage('stop')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_text().strip(), 'offline', result.stderr)
                self.assertTrue((self.d / 'complete').exists())
                self.assertEqual((self.d / 'fixture-final-rc').read_text().strip(), '1')
                if logging == 'writable':
                    self.assertIn('Android-gate-stop-failed', fault.read_text())

    def test_shutdown_retirement_preserves_existing_failure_evidence(self):
        self.state.write_text('offline\n')
        (self.d / 'stop-requested').touch()
        (self.d / 'fault').write_text('REAL_STORE_CHANGED fsc\n')
        self.assertEqual(self.run_stage('--watchdog').returncode, 0)
        self.assertEqual((self.d / 'fault').read_text(), 'REAL_STORE_CHANGED fsc\n')
        self.assertFalse((self.d / 'complete').exists())

    def test_crash_and_total_deadline_end_trial(self):
        (self.d / 'start-attempted').touch()
        self.state.write_text('crashed\n')
        result = self.run_stage('--watchdog')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        (self.d / 'fault').unlink()
        (self.d / 'watchdog-stopped').unlink()
        (self.d / 'watchdog-ready').unlink()
        (self.d / 'start-attempted').unlink()
        self.state.write_text('running\n')
        process = subprocess.Popen([shutil.which('sh'), str(self.script), '--watchdog'], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            end = time.monotonic() + 5
            while not (self.d / 'watchdog-ready').exists() and time.monotonic() < end:
                time.sleep(0.01)
            self.assertTrue((self.d / 'watchdog-ready').exists())
            (self.root / 'uptime').write_text('241.0 0.0\n')
            process.communicate(timeout=8)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
        self.assertEqual(self.state.read_text().strip(), 'offline')
        self.assertIn('total-trial-240s-deadline', (self.d / 'fault').read_text())

    def test_independent_guardian_resets_if_monitor_never_completes(self):
        result = self.run_stage('--guardian')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setprop sys.powerctl reboot,bootloader', self.commandlog.read_text())
        self.assertIn('reboot-fixture bootloader', self.commandlog.read_text())

    def test_completed_trial_disarms_independent_guardian(self):
        (self.d / 'complete').touch()
        self.assertEqual(self.run_stage('--guardian').returncode, 0)
        self.assertFalse(self.commandlog.exists())
        self.assertFalse((self.d / 'guardian-stop.pid').exists())

    def test_hard_guardian_resets_before_blocked_orderly_stop_is_released(self):
        # Override only the stop dependency in this copied fixture, leaving
        # production guardian branching/process launch and fallback unchanged.
        # A synchronous stop would block forever and fail communicate(timeout).
        hook = '''
stop_modem() {
    echo entered > "$D/blocked-stop-entered"
    while [ ! -e "$D/blocked-stop-release" ]; do command sleep 0.01; done
    echo released > "$D/blocked-stop-released"
}
sleep() {
    if [ "$1" = 270 ]; then
        n=0
        while [ ! -e "$D/blocked-stop-entered" ]; do
            command sleep 0.01; n=$((n+1)); [ "$n" -lt 500 ] || return 1
        done
    fi
    command sleep 0.01
}
'''
        self.script.write_text(self.script.read_text().replace('case "${1:-}" in', hook + '\ncase "${1:-}" in', 1))
        process = subprocess.Popen([shutil.which('sh'), str(self.script), '--guardian'], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=8)
            self.assertNotEqual(process.returncode, 0, stdout + stderr)
            self.assertTrue((self.d / 'blocked-stop-entered').exists())
            self.assertFalse((self.d / 'blocked-stop-release').exists())
            self.assertFalse((self.d / 'blocked-stop-released').exists())
            self.assertIn('setprop sys.powerctl reboot,bootloader', self.commandlog.read_text())
            self.assertIn('reboot-fixture bootloader', self.commandlog.read_text())
        finally:
            # The worker's stdio goes to its own log, so a blocked child cannot
            # hold communicate() open. Release it and verify fixture cleanup.
            (self.d / 'blocked-stop-release').touch()
            end = time.monotonic() + 5
            while not (self.d / 'blocked-stop-released').exists() and time.monotonic() < end:
                time.sleep(0.01)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
        self.assertTrue((self.d / 'blocked-stop-released').exists())

    def test_failed_bounded_stop_requests_independent_reset(self):
        self.stuck = True
        # Scale only this fixture's real timeout; execute the same production
        # failure branch, without waiting twenty seconds in an offline test.
        self.script.write_text(self.script.read_text().replace('timeout --foreground -s KILL "$stop_limit" ', 'timeout --foreground -s KILL 0.1 '))
        (self.d / 'abort').touch()
        result = self.run_stage('--watchdog')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setprop sys.powerctl reboot,bootloader', self.commandlog.read_text())
        self.assertFalse((self.d / 'complete').exists())

    def test_lost_modem_identity_and_failed_logging_still_request_reset(self):
        (self.of / 'compatible').unlink()
        (self.d / 'fault').mkdir()  # appending the fallback log itself fails
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'lost-modem-test.sh'
        probe.write_text(functions + '\nstop_modem\n')
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setprop sys.powerctl reboot,bootloader', self.commandlog.read_text())
        self.assertIn('reboot-fixture bootloader', self.commandlog.read_text())

    def prepare_fixture_stores(self):
        self.state.write_text('offline\n')
        parts = self.root / 'dev/block/by-name'
        parts.mkdir(parents=True)
        for part in ('modemst1', 'modemst2', 'fsg', 'fsc'):
            (parts / part).write_bytes(bytes(131072 if part == 'fsc' else 2097152))
            sysdev = self.root / 'sys/class/block' / part
            sysdev.mkdir(parents=True)
            (sysdev / 'size').write_text('256\n' if part == 'fsc' else '4096\n')
        (self.d / 'stores').mkdir()
        return parts

    def test_failed_or_empty_hash_never_becomes_accepted_snapshot_digest(self):
        self.prepare_fixture_stores()
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'bad-hash-test.sh'
        for body in ('return 1', 'return 0', 'printf "junk\\n"; return 0'):
            with self.subTest(body=body):
                shutil.rmtree(self.d / 'stores/pre', ignore_errors=True)
                probe.write_text(functions + '\nsha256sum() { ' + body + '; }\nstores pre\n')
                result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.d / 'stores/pre/modemst1.sha256').exists())

    def test_real_store_comparison_uses_only_fixture_regular_files(self):
        parts = self.prepare_fixture_stores()
        functions = self.script.read_text().split('case "${1:-}" in')[0]
        probe = self.root / 'store-test.sh'
        probe.write_text(functions + '\nstores pre\nstores post\n')
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.rmtree(self.d / 'stores/post')
        with (parts / 'fsc').open('r+b') as f:
            f.write(b'CHANGED')
        probe.write_text(functions + '\nstores post\n')
        result = subprocess.run([shutil.which('sh'), str(probe)], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REAL_STORE_CHANGED fsc', (self.d / 'fault').read_text())
        self.assertEqual((parts / 'fsc').read_bytes()[:7], b'CHANGED')  # never repaired

    def test_real_store_comparison_accepts_toybox_block_device_output(self):
        self.env.update(FIXTURE_BLOCK_KIND='block device', FIXTURE_STAT_STYLE='toybox')
        self.test_real_store_comparison_uses_only_fixture_regular_files()


if __name__ == '__main__':
    unittest.main(verbosity=2)
