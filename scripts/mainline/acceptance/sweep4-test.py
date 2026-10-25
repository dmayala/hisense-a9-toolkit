#!/usr/bin/env python3
"""Five offline groups: synthetic libmtp backend and fake owner transports."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ASSETS = Path('/home/user/A9-build/mainline/acceptance-sweep-20261023')
CC = '/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc'
INCLUDE = '/nix/store/kicl0w76cdfrg322blnm4w9acz5lrm1p-libmtp-1.1.22-dev/include'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


fake = load('sweep4_fakes',HERE/'selftest.py')
root = load('sweep4_root',HERE.parent/'boot-deadline/onb-root.py')


class Tests(unittest.TestCase):
    def test_1_real_probe_refuses_reset_and_requires_actual_storage_reply(self):
        # Preload ALL libmtp entry points; no real USB enumeration occurs.
        # Backend reset aborts if executable interposition ever fails.
        source = r'''
#include <libmtp.h>
#include <stdlib.h>
#include <string.h>
struct libusb_device_handle;
int libusb_reset_device(struct libusb_device_handle *p) { (void)p; abort(); }
static int mode(const char *s) { return !strcmp(getenv("FIXTURE"),s); }
void LIBMTP_Init(void) {}
LIBMTP_error_number_t LIBMTP_Detect_Raw_Devices(LIBMTP_raw_device_t **p,int *n) {
 *n=mode("ambiguous")?2:1; *p=calloc(*n,sizeof(**p)); return LIBMTP_ERROR_NONE;
}
LIBMTP_mtpdevice_t *LIBMTP_Open_Raw_Device_Uncached(LIBMTP_raw_device_t *p) {
 (void)p; if(mode("open-fail"))return NULL;
 LIBMTP_mtpdevice_t *d=calloc(1,sizeof(*d)); d->storage=calloc(1,sizeof(*d->storage));
 d->storage->id=mode("empty")?0:1; return d;
}
int LIBMTP_Get_Storage(LIBMTP_mtpdevice_t *p,int sort) { (void)p; (void)sort; return 0; }
void LIBMTP_Release_Device(LIBMTP_mtpdevice_t *p) {
 if(libusb_reset_device((void*)1)!=-12)abort(); free(p->storage); free(p);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'fake.c').write_text(source)
            sp.run([CC,'-shared','-fPIC','-I'+INCLUDE,p/'fake.c','-o',p/'fake.so'],check=True)
            for mode in ('ok','empty','ambiguous','open-fail'):
                with self.subTest(mode=mode):
                    r = sp.run([ASSETS/'mtp-session'],env=dict(os.environ,LD_PRELOAD=str(p/'fake.so'),FIXTURE=mode),capture_output=True,text=True,timeout=10)
                    self.assertEqual(r.returncode == 0,mode == 'ok',r.stderr)
                    self.assertIn('MTP_SESSION '+str(int(mode == 'ok')),r.stdout)
                    if mode in ('ok','empty'): self.assertIn('MTP_RESETS_REFUSED 1',r.stdout)

    def setup_fake(self, tmp):
        env = patch.dict(os.environ,{'A9_BTRY_LOGS':tmp,'A9_SWEEP_SECONDS':'3300'},clear=True)
        env.start(); self.addCleanup(env.stop)
        clock = fake.Clock()
        for name in ('monotonic','sleep'):
            p = patch.object(fake.sweep.time,name,getattr(clock,name)); p.start(); self.addCleanup(p.stop)
        return fake.Fake()

    def test_2_transient_adb_and_nc_reconnect_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.setup_fake(tmp); reads = []; probes = []
            def shell(source, **kwargs):
                reads.append(source)
                return (1,'') if len(reads)<3 else (0,'_b\n1\n')
            def native(source, **kwargs):
                probes.append(source)
                return (1,'') if len(probes)<2 else (0,'native_ready 1\n')
            with patch.object(s,'shell',side_effect=shell),patch.object(s,'native',side_effect=native):
                self.assertTrue(s.transport_ready())
            self.assertEqual(set(reads),{'getprop ro.boot.slot_suffix; getprop sys.boot_completed'})
            self.assertEqual(set(probes),{'printf "native_ready 1\\n"'})

    def test_3_lost_native_transport_blocks_reboot_without_losing_prior_features(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            s = self.setup_fake(tmp); native = s.native
            def failed(source,**kwargs):
                return (1,'') if source == 'printf "native_ready 1\\n"' else native(source,**kwargs)
            with patch.object(s,'native',side_effect=failed),patch.object(fake.sweep.play.Hook,'credentials',return_value=['SECRET_TEST_SSID','SECRET_TEST_PASSWORD']):
                s.execute()
            self.assertEqual(s.results['08_22_sensors']['status'],'PASS')
            self.assertEqual(s.results['19_front_photo']['status'],'PASS')
            self.assertEqual(s.results['11_adb_mtp']['status'],'FAIL')
            self.assertEqual(s.results['11_adb_mtp']['evidence']['transport_ready'],0)
            self.assertEqual(s.results['A_stability']['status'],'FAIL')
            self.assertFalse(s.rebooted)

    def test_4_native_helper_phase_diagnostics_survive_stderr_suppression(self):
        for mode, phase, code in [('push',1,1),('timeout',2,124),('remote',3,0)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                def transport(argv,**kwargs):
                    if argv[1] == 'push': return sp.CompletedProcess(argv,1 if mode == 'push' else 0,'')
                    if mode == 'timeout': raise sp.TimeoutExpired(argv,1)
                    return sp.CompletedProcess(argv,0,'A9_ONB_NATIVE_EXIT rc=7 line=9\n__A9_ONB_RC=7\n')
                out = io.StringIO()
                with patch.dict(os.environ,{'A9_BTRY_IN_HOOK':'1','ADB':'FAKE','A9_BTRY_LOGS':tmp},clear=True),patch.object(root.sys,'stdin',io.StringIO('echo fixture')),patch.object(root.sp,'run',side_effect=transport),contextlib.redirect_stdout(out),contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(Exception): root.main()
                self.assertIn(f'A9_ONB_TRANSPORT phase={phase} rc={code}',out.getvalue())
                self.assertTrue((Path(tmp)/'onb-root-failed-command.sh').is_file())

    def test_5_real_data_failure_keeps_its_cause(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            s = self.setup_fake(tmp); s.ram_data = True; s.basic()
            e = s.results['01_boot']['evidence']
            self.assertEqual(s.results['01_boot']['status'],'FAIL')
            self.assertEqual((e['real_data_required'],e['data_ready'],e['data_rc'],e['data_tmpfs']),(1,0,25,1))
            self.assertEqual(s.results['29_wrapped_fbe']['status'],'FAIL')


if __name__ == '__main__': unittest.main(verbosity=2)
