#!/usr/bin/env python3
"""Five focused groups: actual firmware/ADSP-selection shell, Android mksh/cp."""
from pathlib import Path
import hashlib,os,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
SOURCE=(ROOT/'scripts/mainline/android/vendor/bin/a9-sensors.sh').read_text()
FIRMWARE=SOURCE[SOURCE.index('# 2. firmware into'):SOURCE.index('# 3. modules')]
SELECT=SOURCE[SOURCE.index('RP= count=0'):SOURCE.index('[ "$(cat "$RP/state")" = offline')]
SHELL='/home/user/A9-build/mainline/integrated-20261021n/r4-tests/android-sh'
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
SYSROOT='/home/user/A9-build/mainline/integrated-20261021n/r4-tests/sysroot'
LIBS='/home/user/A9-build/mainline/android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/lib64'
TOYBOX='/home/user/A9-build/mainline/android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/bin/toybox'

class Firmware(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.r=Path(self.tmp.name);self.v=self.r/'vendor';self.fwd=self.r/'bridge'
        self.sys=self.r/'sys';self.data=self.r/'data'
        for base in (self.v/'firmware/a9adsp',self.v/'firmware'):
            f=base/'qcom/sm6115/adsp.mdt';f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'public firmware fixture\n')
        self.relative='qcom/sm6115/adsp.mdt'
        self.vendor=self.v/'firmware'/self.relative
        digest=hashlib.sha256(self.vendor.read_bytes()).hexdigest()
        (self.v/'firmware/a9adsp.SHA256SUMS').write_text(digest+'  ./'+self.relative+'\n')
        self.param=self.sys/'module/firmware_class/parameters/path';self.param.parent.mkdir(parents=True);self.param.write_text(str(self.fwd)+'\n')
        self.stubs=self.r/'stubs';self.stubs.mkdir()
        cp=self.stubs/'cp';cp.write_text(f'#!/bin/sh\necho copy >> "{self.r}/copies"\nexec {QEMU} -L {SYSROOT} -E LD_LIBRARY_PATH={LIBS} {SYSROOT}/system/bin/linker64 {TOYBOX} cp "$@"\n');cp.chmod(0o755)
        self.env=dict(os.environ,PATH=str(self.stubs)+':'+os.environ['PATH'])

    def invoke(self,block=FIRMWARE):
        text=f'SYS={self.sys}\nVND={self.v}\nDATA={self.data}\nlog() {{ :; }}\nfail() {{ echo "FAIL:$1"; exit 42; }}\n'+block
        f=self.r/'test.sh';f.write_text(text)
        return subprocess.run([SHELL,str(f)],env=self.env,capture_output=True,text=True)

    def bridge(self):
        self.fwd.mkdir();(self.fwd/'qcom').symlink_to(self.v/'firmware/qcom');self.vendor.chmod(0o444)

    def test_bridge_negative_control_and_reuse(self):
        self.bridge();original=self.vendor.stat().st_mtime_ns
        old='P=$SYS/module/firmware_class/parameters/path; FWD=$(cat "$P"); mkdir -p "$FWD" && cp -R "$VND/firmware/a9adsp/." "$FWD/" && (cd "$FWD" && sha256sum -c "$VND/firmware/a9adsp.SHA256SUMS") || fail firmware-copy\n'
        p=self.invoke(old);self.assertEqual(p.returncode,42,p.stderr);self.assertIn('FAIL:firmware-copy',p.stdout)
        self.assertEqual(self.vendor.stat().st_mtime_ns,original)
        (self.r/'copies').unlink()
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertFalse((self.r/'copies').exists());self.assertEqual(self.vendor.stat().st_mtime_ns,original)

    def test_fresh_ram_fallback(self):
        self.param.write_text('')
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.param.read_text(),str(self.data/'local/tmp/a9fw'))
        self.assertEqual((self.data/'local/tmp/a9fw'/self.relative).read_bytes(),self.vendor.read_bytes())

    def test_persistent_copy_reuse_and_repair(self):
        f=self.fwd/self.relative;f.parent.mkdir(parents=True);f.write_bytes(self.vendor.read_bytes())
        original=f.stat().st_mtime_ns
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertFalse((self.r/'copies').exists());self.assertEqual(f.stat().st_mtime_ns,original)
        f.write_bytes(b'stale prior boot')
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertTrue((self.r/'copies').exists());self.assertEqual(f.read_bytes(),self.vendor.read_bytes())

    def test_bad_bridge_refuses_without_copy(self):
        self.bridge();manifest=self.v/'firmware/a9adsp.SHA256SUMS';manifest.write_text('0'*64+'  ./'+self.relative+'\n')
        original=self.vendor.read_bytes();p=self.invoke()
        self.assertEqual(p.returncode,42);self.assertIn('FAIL:firmware-bridge-hash',p.stdout)
        self.assertFalse((self.r/'copies').exists());self.assertEqual(self.vendor.read_bytes(),original)

    def test_adsp_selected_beside_running_modem(self):
        def node(index,address,compatible,state):
            p=self.sys/'class/remoteproc'/('remoteproc'+str(index));(p/'device').mkdir(parents=True)
            of=self.sys/'firmware/devicetree/base/soc@0'/('remoteproc@'+address);of.mkdir(parents=True)
            (of/'compatible').write_bytes(compatible.encode()+b'\0');(p/'device/of_node').symlink_to(of);(p/'state').write_text(state+'\n');return p
        modem=node(0,'6080000','qcom,sm6115-mpss-pas','running');adsp=node(1,'a400000','qcom,sm6115-adsp-pas','offline')
        p=self.invoke(SELECT+'\n[ "$RP" = "'+str(adsp)+'" ]\n');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual((modem/'state').read_text(),'running\n')
        node(2,'a400000-copy','qcom,sm6115-adsp-pas','offline');p=self.invoke(SELECT)
        self.assertEqual(p.returncode,42);self.assertIn('FAIL:remoteproc-count',p.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
