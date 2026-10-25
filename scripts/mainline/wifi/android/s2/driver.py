#!/usr/bin/env python3
"""Frozen S2 coordinator: --verify offline, --probe public hardware gates,
--run 1 credential delivery/association. No tuple, credential or restore overrides.
Probe/run evidence uses unique directories below roots fixed in the release.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

sys.dont_write_bytecode=True
STORES=('modemst1','modemst2','fsg','fsc','persist')
BUNDLE_FILES=('bridge.jar','s2-target.sh','reg.awk','channel.awk','power.awk','s2-encode.py','provenance.json')
class Refused(Exception):pass

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    return h.hexdigest()

def checked_file(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():raise Refused('seal-file')
    return p

def verify(path,expected):
    p=checked_file(path)
    if digest(p)!=expected:raise Refused('seal')
    config=json.loads(p.read_bytes())
    if config['schema']!=2 or config['attempt']!=1:raise Refused('seal')
    if any(not Path(config[k]).is_absolute() for k in ('runs_root','probes_root')):raise Refused('seal')
    for alias,target in config.get('aliases',{}).items():
        if str(Path(alias).resolve())!=target:raise Refused('seal-alias')
    for name,h in config['pins'].items():
        if digest(checked_file(name))!=h:raise Refused('seal')
    return config

def read_credentials(op,token_file):
    # Called only after the public hardware probe. Never used by --verify/preparation.
    token=Path(token_file).read_text().strip()
    if not token or token=='FILL_ME':raise Refused('credential-source')
    env={k:os.environ[k] for k in ('PATH','HOME') if k in os.environ}
    env.update(OP_SERVICE_ACCOUNT_TOKEN=token,OP_CACHE='false',OP_BIOMETRIC_UNLOCK_ENABLED='false')
    values=[]
    for field in ('network name','wireless network password'):
        r=subprocess.run([op,'read','--no-newline','op://vault/item/field'+field],
                         stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=20,check=True)
        if len(r.stdout)>128:raise Refused('credential-source')
        values.append(r.stdout.decode('utf-8'))
    return json.dumps(dict(ssid=values[0],passphrase=values[1])).encode()

def store_rows(raw):
    rows=re.findall(rb'^S2_STORE (modemst1|modemst2|fsg|fsc|persist) ([0-9a-f]{64})$',raw,re.M)
    if len(rows)!=5 or {x.decode() for x,h in rows}!=set(STORES):raise Refused('store-read')
    return {x.decode():h.decode() for x,h in rows}

AUDIT_HELPERS=r'''
audit_fail() { printf 'S2_AUDIT gate=%s result=failed\n' "$1"; exit 1; }
'''
MPSS_CHECK=r'''
n=0
mpss_proof=offline
for p in /sys/class/remoteproc/remoteproc*; do
    [ -r "$p/device/of_node/compatible" ] || continue
    if tr '\000' '\n' < "$p/device/of_node/compatible" 2>/dev/null | grep -qx 'qcom,sm6115-mpss-pas'; then
        n=$((n+1))
        [ "$(cat "$p/state")" = offline ] || audit_fail mpss-state
    fi
done
if [ "$n" != 1 ]; then
    [ "$n" = 0 ] && [ "${mpss_allow_unbound:-0}" = 1 ] || audit_fail mpss-count
    # Before session prepare, this module is deliberately absent. The pinned
    # SM6115 descriptor has auto_boot=false. Require the unique platform device
    # to exist AND be unbound; absence of a remoteproc alone proves nothing.
    [ ! -d /sys/module/qcom_q6v5_pas ] || audit_fail mpss-module
    devices=0
    for p in /sys/bus/platform/devices/*; do
        [ -r "$p/of_node/compatible" ] || continue
        if tr '\000' '\n' < "$p/of_node/compatible" 2>/dev/null | grep -qx 'qcom,sm6115-mpss-pas'; then
            devices=$((devices+1))
            [ ! -e "$p/driver" ] && [ ! -L "$p/driver" ] || audit_fail mpss-bound
        fi
    done
    [ "$devices" = 1 ] || audit_fail mpss-platform
    mpss_proof=unbound
fi
'''
OFFLINE_MPSS='mpss_allow_unbound=0\n'+MPSS_CHECK
PRE_MPSS='mpss_allow_unbound=1\n'+MPSS_CHECK
RAM_SLOT=r'''
[ "$(getprop ro.boot.slot_suffix)" = _b ] || audit_fail slot
[ "$(getprop ro.a9ml.wifi.session)" = 1 ] || audit_fail session-flag
case "$(uname -r)" in 7.*) ;; *) audit_fail kernel;; esac
awk '$2=="/data" {n++;if($1!="/dev/block/ram0" || $3!="ext4")bad=1} END {exit !(n==1 && !bad)}' /proc/mounts || audit_fail ram-data
'''
HASH_STORES=r'''
for f in modemst1 modemst2 fsg fsc persist; do
    [ -r "/dev/block/by-name/$f" ] || audit_fail "store-open-$f"
    if h=$(timeout 20 sha256sum < "/dev/block/by-name/$f"); then rc=0; else rc=$?; fi
    printf 'S2_AUDIT gate=store-read store=%s rc=%s\n' "$f" "$rc"
    [ "$rc" = 0 ] || audit_fail "store-read-$f"
    h=${h%% *}
    case "$h" in ''|*[!0-9a-f]*) audit_fail "store-digest-$f";; esac
    [ "${#h}" = 64 ] || audit_fail "store-digest-$f"
    printf 'S2_STORE %s %s\n' "$f" "$h"
done
'''
PRE=r'''
[ "$(getprop vendor.a9ml.wifi.session.state)" = awaiting-s2-audit ] || audit_fail session-state
service_state=$(getprop init.svc.a9ml_wifi_session) || audit_fail session-service-read
case "$service_state" in
    '') service_label=unpublished;;
    stopped|running|stopping|restarting) service_label=$service_state;;
    *) service_label=other;;
esac
printf 'S2_AUDIT gate=session-service state=%s\n' "$service_label"
case "$service_label" in
    unpublished)
        # A disabled service need not publish init.svc until its first state
        # transition. Accept that only with no PID, boot history or start request.
        service_pid=$(getprop init.svc_debug_pid.a9ml_wifi_session) || audit_fail session-service-read
        case "$service_pid" in ''|0) ;; *) audit_fail session-service-pid;; esac
        service_boot=$(getprop ro.boottime.a9ml_wifi_session) || audit_fail session-service-read
        [ -z "$service_boot" ] || audit_fail session-service-history
        ;;
    stopped) ;;
    *) audit_fail session-service;;
esac
start_requested=$(getprop vendor.a9ml.wifi.s2.start) || audit_fail session-service-read
case "$start_requested" in ''|0) ;; *) audit_fail session-start-request;; esac
[ ! -e /mnt/a9-wifi/session ] || audit_fail session-directory
for f in /sys/module/a9_eink/parameters/enable /sys/module/panel_hisense_hlte556n_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on; do
    read -r value < "$f" || audit_fail display-read
    case "$value" in N|0) ;; *) audit_fail display-open;; esac
done
'''
STOP=r'''
/vendor/bin/a9ml-wifi-session.sh stop >/dev/null 2>&1
case "$(getprop vendor.a9ml.wifi.session.state)" in ended|failed|awaiting-s2-audit) ;; *) exit 1;; esac
'''+OFFLINE_MPSS+"echo 'S2_STOP offline=yes'\n"

class Driver:
    def __init__(self,config,probe=False):
        self.c=config;self.probe_mode=probe
        self.e=Path(config.get('evidence',str(Path(config.get('runs_root','.'))/'unallocated/evidence')))
        self.private=Path(config.get('private',str(Path(config.get('runs_root','.'))/'unallocated/private')))
        self.pattern=self.private/'patterns';self.log_path=self.e/'driver.log'
        self.boot=None;self.boot_attempted=False;self.pre=None;self.post=None;self.pattern_ready=False
        self.lock_fd=None;self.serial='';self.env={};self.phase='entry';self.failed=False;self.started_at=None;self.boot_writers_done=True
        self.framework_pending=False
    def marker_exists(self,path):
        p=Path(path);return p.exists() or p.is_symlink()
    def delivery_fence(self):return Path(self.c['permit']+'.pending')
    def used(self):return self.marker_exists(self.c['permit']) or self.marker_exists(self.delivery_fence())
    def sync_parent(self,path):
        fd=os.open(str(Path(path).parent),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def marker(self,path,data):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        self.sync_parent(path)
    def allocate(self):
        # The CLI selects only roots from the sealed config. Allocate while
        # holding USB ownership; preserve every pre-delivery run for diagnosis.
        if 'execution_root' in self.c:
            root=Path(self.c['execution_root'])
            if root.is_symlink():raise Refused('execution-root')
            root.mkdir(mode=0o700,exist_ok=True)
            info=root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise Refused('execution-root')
            run=Path(tempfile.mkdtemp(prefix='probe-' if self.probe_mode else 'run-',dir=root))
            self.e=run/'evidence';self.private=run/'private'
            self.pattern=self.private/'patterns';self.log_path=self.e/'driver.log'
        self.e.mkdir(mode=0o700);self.private.mkdir(mode=0o700)
    def log(self,label):
        # Callers use constants/enums only. Never append exception or transport text.
        line='S2_DRIVER '+label+'\n'
        with self.log_path.open('a') as f:f.write(line)
        print(line,end='',flush=True)
    def write(self,name,data):
        with (self.e/name).open('wb') as f:f.write(data)
    def call(self,argv,*,input=None,timeout=60,log=None,status=None):
        try:
            r=subprocess.run(argv,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             timeout=timeout,env=self.env,close_fds=True)
        except subprocess.TimeoutExpired as error:
            if status:status(None,error.stdout or b'',True)
            if log:self.write(log,(error.stdout or b'')+(error.stderr or b''))
            raise Refused('command-timeout') from None
        if status:status(r.returncode,r.stdout,False)
        if log:self.write(log,r.stdout+r.stderr)
        if r.returncode:raise Refused('command')
        return r.stdout
    @staticmethod
    def root_diagnostics(out):
        # Full-line allowlist: no generic exception/transport text is ever copied
        # into these public reasons. Keep first failure before cleanup phases.
        phase=r'(?:entry|runtime-hashes|binder-api|baseline|enable|probe-connectivity|scan-identity|scan-start|scan-public-results|input|scan-wait|scan-results|scan-select|connect-dispatch|association|device-mac|connectivity-api|dhcp|clock|https|browser|disable|forget|runtime)'
        selection=r'(?:accepted|ambiguous|no-rows|row-format|ssid-absent|frequency-mismatch|security-mismatch|age-rejected)'
        operation=r'(?:list-networks|status|set-wifi-enabled|start-scan|list-scan-results|connect-network|forget-network)'
        branch=r'(?:callback-timeout|security|argument|service-return|reply-timeout|reply-io|exception)'
        grammar=rb'^S2_BRIDGE (?:phase='+phase.encode()+rb'|result=failed phase='+phase.encode()+rb'|select='+selection.encode()+rb' rows=[0-9]{1,6} parsed=[0-9]{1,6} named=[0-9]{1,6} frequency=[0-9]{1,6} security=[0-9]{1,6} fresh=[0-9]{1,6}|command='+operation.encode()+rb' result=failed branch='+branch.encode()+rb'(?: rc=-?[0-9]{1,6})?)\r?$'
        lines=[m[0].decode().rstrip('\r') for m in re.finditer(grammar,out,re.M)]
        gates=re.findall(rb'^S2_GATE phase=(?:entry|admission|awk-pin|ram-data|ram-swap|ram-evidence|private-paths|review-seal|iw-pin|remaining-budget|receive|probe|public-scan|framework-connect|framework-dhcp-web|regulatory|channel|power-limit|power-before|power-after) result=failed\r?$',out,re.M)
        lines.extend(x.decode().rstrip('\r') for x in gates)
        reasons=[]
        for line in lines:
            m=re.fullmatch(r'S2_BRIDGE select=('+selection+r') rows=.*',line)
            if m and m[1]!='accepted':reasons.append('scan-select-'+m[1])
            m=re.fullmatch(r'S2_BRIDGE command=('+operation+r') result=failed branch=('+branch+r')(?: rc=-?[0-9]{1,6})?',line)
            if m:reasons.append('command-'+m[1]+'-'+m[2])
            m=re.fullmatch(r'S2_BRIDGE result=failed phase=('+phase+r')',line)
            if m:reasons.append('bridge-'+m[1])
        if not reasons and gates:reasons.append('gate-'+gates[0].decode().split('phase=',1)[1].split(' ',1)[0])
        return lines, reasons[0] if reasons else 'worker-or-root-command'

    def root(self,script,timeout=45,capture=None):
        # No private payload on argv. The remote rc is checked independently of nc/adb's rc.
        payload=b'set +x\nset +e\n(\nset -eu\n'+AUDIT_HELPERS.encode()+b'[ "$(id -u)" = 0 ] || audit_fail root-uid\n'+script.encode()+b'\n)\nr=$?\nprintf "S2_ROOT_RC %s\\n" "$r"\nexit "$r"\n'
        def record(rc,out,timed_out):
            # Never retain raw transport output/errors here: they can contain
            # identities. Only fixed audit grammar and numeric exit codes.
            marker=re.search(rb'^S2_ROOT_RC ([0-9]+)\r?$',out,re.M)
            gate=rb'(?:root-uid|slot|session-flag|kernel|ram-data|session-state|session-service(?:-read|-pid|-history)?|session-start-request|session-directory|display-read|display-open|mpss-state|mpss-count|mpss-module|mpss-bound|mpss-platform|store-(?:open|read|digest)-(?:modemst1|modemst2|fsg|fsc|persist))'
            audit=re.findall(rb'^S2_AUDIT gate='+gate+rb' result=failed\r?$',out,re.M)
            reads=re.findall(rb'^S2_AUDIT gate=store-read store=(?:modemst1|modemst2|fsg|fsc|persist) rc=[0-9]+\r?$',out,re.M)
            proofs=re.findall(rb'^S2_AUDIT gate=mpss branch=(?:offline|unbound)\r?$',out,re.M)
            services=re.findall(rb'^S2_AUDIT gate=session-service state=(?:unpublished|stopped|running|stopping|restarting|other)\r?$',out,re.M)
            diagnostics,reason=self.root_diagnostics(out)
            event={'diagnostics':diagnostics,'rejection_reason':'transport-timeout' if timed_out else 'transport-exit' if rc else 'missing-sentinel' if not marker else reason if marker[1]!=b'0' else None,'phase':self.phase,'transport_rc':rc,'timeout':timed_out,'root_rc':int(marker[1]) if marker else None,
                   'audit':[x.decode().rstrip('\r') for x in audit+proofs+services+reads]}
            with (self.e/'root-status.jsonl').open('a') as f:f.write(json.dumps(event,sort_keys=True)+'\n')
        out=self.call([self.c['tools']['adb'],'-s',self.serial,'shell','-T','nc 127.0.0.1 2324'],input=payload,timeout=timeout,log=capture,status=record)
        m=re.search(rb'\n?S2_ROOT_RC ([0-9]+)\r?\n?$',out)
        if not m or len(re.findall(rb'^S2_ROOT_RC ',out,re.M))!=1:
            self.log('root_response=invalid reason=missing-or-duplicate-sentinel');raise Refused('root-command')
        if m[1]!=b'0':
            self.log('root_response=rejected reason='+self.root_diagnostics(out)[1]);raise Refused('root-command')
        body=out[:m.start()].replace(b'\r\n',b'\n')
        # Public audit diagnostics are already retained by record(). Do not
        # change the exact command-result grammar used by start/idle cleanup.
        body=re.sub(rb'^S2_AUDIT gate=session-service state=(?:unpublished|stopped|running|stopping|restarting|other)\n',b'',body,flags=re.M)
        return body.rstrip(b'\n')+b'\n'
    def wait_root(self,seconds=60):
        end=time.monotonic()+seconds
        while True:
            try:
                if self.root("echo 'S2_ROOT_READY uid=0'",min(5,max(.1,end-time.monotonic())))==b'S2_ROOT_READY uid=0\n':
                    self.log('root_channel=loopback ready=yes uid=0');return
            except Refused:pass
            if time.monotonic()>=end:raise Refused('root-ready')
            time.sleep(min(1,max(0,end-time.monotonic())))
    def setup_env(self):
        self.env={k:os.environ[k] for k in ('PATH','HOME','LANG','XDG_RUNTIME_DIR') if k in os.environ}
        self.env.update(self.c['env'])
        self.env.update(A9_HOME=str(self.e/'host'),A9_USB_LOCKED='1',A9_USB_LOCK_OWNER=str(os.getpid()),
                        A9_DEVICE_JSON=self.c['device_json'],ADB=self.c['tools']['adb'],FB=self.c['tools']['fastboot'])
        out=self.call([self.c['tools']['python3'],'-I',self.c['repo']+'/scripts/a9_devices.py','resolve'],timeout=15)
        self.serial=out.decode().strip()
        if not re.fullmatch(r'[A-Za-z0-9._-]+',self.serial) or self.serial.startswith('unknown-'):raise Refused('device')
        self.env.update(A9_SERIAL=self.serial,ANDROID_SERIAL=self.serial)
    def acquire_lock(self,seconds=120):
        # Start the adb server before taking the fd, and never let children inherit it.
        subprocess.run([self.c['tools']['adb'],'start-server'],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,close_fds=True)
        path=Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))/'a9-usb.lock'
        self.lock_fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        info=os.fstat(self.lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():raise Refused('usb-lock')
        end=time.monotonic()+seconds
        while True:
            try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return
            except BlockingIOError:
                if time.monotonic()>=end:raise Refused('usb-lock-timeout')
                time.sleep(min(.2,max(0,end-time.monotonic())))
    def boot_args(self):
        return [self.c['tools']['bash'],self.c['repo']+'/scripts/mainline/android/android-boot.sh',
                '--wifi','--eink','--s2-quiet','--boot',self.c['boot'],'--vendor',self.c['vendor'],
                '--hold','5400','--no-restore']
    def health(self,name):
        out=self.call([self.c['tools']['bash'],self.c['repo']+'/scripts/a9-health.sh','--json'],timeout=180,log=name)
        h=json.loads(out)
        if h.get('mode')!='android' or h.get('slot')!='_a' or str(h.get('boot_completed'))!='1':raise Refused('health')
    def slot_a_stores(self,name):
        try:
            text='for f in modemst1 modemst2 fsg fsc persist; do h=$(su -c "sha256sum /dev/block/by-name/$f") || exit; printf "S2_STORE %s %s\\n" "$f" "${h%% *}"; done'
            out=self.call([self.c['tools']['adb'],'-s',self.serial,'shell',text],timeout=180)
            values=store_rows(out);self.write(name,(json.dumps(values,sort_keys=True)+'\n').encode());return values
        except Exception:
            self.log('slot_a_stores=unavailable informational=yes');return None
    def start_boot(self):
        check=self.call(self.boot_args(),timeout=900,log='boot-check.log')
        found=re.findall(rb'^CHECK: PASS\. Approval code for exactly these images: ([0-9a-f]{8})$',check,re.M)
        if len(found)!=1:raise Refused('boot-check')
        self.boot_attempted=True;self.boot_writers_done=False
        with (self.e/'boot.log').open('wb') as out:
            self.boot=subprocess.Popen(self.boot_args()+['--write','--code',found[0].decode()],
                stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,env=self.env,
                start_new_session=True,close_fds=True)
        end=time.monotonic()+2400
        while time.monotonic()<end:
            if self.boot.poll() is not None:raise Refused('boot-exited')
            if b'S2_QUIET_READY\n' in (self.e/'boot.log').read_bytes():return
            time.sleep(3)
        raise Refused('boot-timeout')
    def live_stores(self,before):
        unstarted=before or self.started_at is None
        script=RAM_SLOT+(PRE if unstarted else '')+(PRE_MPSS if unstarted else OFFLINE_MPSS)+"printf 'S2_AUDIT gate=mpss branch=%s\\n' \"$mpss_proof\"\n"+HASH_STORES
        out=self.root(script,150)
        try:values=store_rows(out)
        except Refused:
            self.log('store_parse=failed');raise
        self.write('stores-b-pre.json' if before else 'stores-b-post.json',(json.dumps(values,sort_keys=True)+'\n').encode())
        return values
    def install(self):
        bundle=Path(self.c['bundle']);tar=io.BytesIO()
        with tarfile.open(fileobj=tar,mode='w') as archive:
            for name in BUNDLE_FILES:
                data=(bundle/name).read_bytes();info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o400;info.uid=info.gid=0
                archive.addfile(info,io.BytesIO(data))
            data=b'I_AUTHORIZE_REVIEWED_ANDROID_S2\ncountry=US frequency=2427 persistent_userdata=no\n'+(bundle/'SHA256SUMS').read_bytes()
            info=tarfile.TarInfo('GO');info.size=len(data);info.mode=0o400;info.uid=info.gid=0;archive.addfile(info,io.BytesIO(data))
        encoded=base64.b64encode(tar.getvalue()).decode()
        script=RAM_SLOT+r'''
B=/data/local/tmp/a9-wifi-s2
[ ! -e "$B" ] && [ ! -L "$B" ]
mkdir -m 700 "$B"
TMPDIR=/data/local/tmp
export TMPDIR
base64 -d <<'S2_PUBLIC_BUNDLE' | tar -xf - -C "$B"
'''+encoded+"\nS2_PUBLIC_BUNDLE\nchown -R 0:0 \"$B\"\nchmod 700 \"$B\"\nchmod 400 \"$B\"/*\necho 'S2_INSTALL ready=yes'\n"
        if self.root(script,60)!=b'S2_INSTALL ready=yes\n':raise Refused('install')
    def start_session(self):
        script=RAM_SLOT+PRE+PRE_MPSS+"h=$(sha256sum /vendor/etc/init/init.a9ml-wifi-session.rc); [ \"${h%% *}\" = "+self.c['session_rc_sha256']+" ]\nsetprop vendor.a9ml.wifi.s2.start 1\necho 'S2_START requested=yes'\n"
        self.started_at=time.monotonic()
        if self.root(script)!=b'S2_START requested=yes\n':raise Refused('start')
        end=time.monotonic()+1200
        while time.monotonic()<end:
            out=self.root('getprop vendor.a9ml.wifi.session.state',15).strip()
            if out==b'serving':return
            if out in (b'failed',b'ended'):raise Refused('session-failed')
            time.sleep(3)
        raise Refused('session-timeout')
    def stop_logd(self):
        out=self.root('setprop ctl.stop logd\nfor i in $(seq 1 15); do [ "$(getprop init.svc.logd)" != stopped ] || break; sleep 1; done\n[ "$(getprop init.svc.logd)" = stopped ]\necho "S2_LOGD stopped=yes"',30)
        if out!=b'S2_LOGD stopped=yes\n':raise Refused('logd')
    def worker(self,mode,seconds):
        out=self.root('/system/bin/sh /data/local/tmp/a9-wifi-s2/s2-target.sh '+mode,seconds,capture=mode+'.log')
        self.write(mode+'.log',out)
        required=b'public_preflight=accepted' if mode=='probe' else b'S2_GATE phase=done result=success'
        if required not in out:raise Refused('worker')
        if mode=='probe' and any(label not in out for label in (b'wifi=enabled verified=yes',b'global=US',b'channel=accepted frequency=2427',b'phase=power-before')):raise Refused('probe-gates')
        if mode=='run' and (b'framework_cleanup_rc=0' not in out or b'cleanup=unproved' in out):raise Refused('worker-cleanup')
    def credentials(self):
        if self.probe_mode:raise Refused('probe-credential-boundary')
        return read_credentials(self.c['tools']['op'],self.c['token_file'])
    def deliver(self):
        if self.probe_mode or self.used():raise Refused('delivery-not-authorized')
        raw=self.credentials()
        encoder=Path(self.c['bundle'])/'s2-encode.py'
        spec=importlib.util.spec_from_file_location('s2_encode',encoder);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.encode(raw)  # Canonical validation precedes any private file or delivery.
        data=json.loads(raw);ssid=data['ssid'].encode();password=data['passphrase'].encode()
        patterns=b'\n'.join((ssid,ssid.hex().encode(),password,base64.b64encode(password)))+b'\n'
        fd=os.open(self.pattern,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(patterns)
        self.pattern_ready=True
        # Real CLI pipes exercise the exact FIFO-only adapter, never a payload file.
        payload=self.call([self.c['tools']['python3'],'-I',str(encoder),'--root-stdin'],input=raw,timeout=15)
        # Accounting only: the existing credential wire/encoder/receipt is
        # unchanged. A lost receipt leaves an uncertainty fence, not permission
        # to redeliver. No permit is consumed at any earlier public gate.
        self.marker(self.delivery_fence(),b'S2 delivery outcome pending\n')
        try:
            if self.root(payload.decode(),60)!=b'S2_GATE phase=receive input=ready\n':raise Refused('delivery')
        except BaseException:
            self.log('delivery_receipt=unconfirmed fence=retained');raise
        self.marker(self.c['permit'],b'S2 attempt 1 credential delivery acknowledged\n')
        self.delivery_fence().unlink();self.sync_parent(self.c['permit'])
        self.log('permit=consumed reason=delivery-acknowledged')
        self.log('delivery=accepted')
    def framework_cleanup(self):
        expected=digest(Path(self.c['bundle'])/'bridge.jar')
        script=RAM_SLOT+r'''
B=/data/local/tmp/a9-wifi-s2
[ -f "$B/bridge.jar" ] && [ ! -L "$B/bridge.jar" ]
h=$(sha256sum "$B/bridge.jar")
'''+('[ "${h%% *}" = '+expected+' ]\n')+r'''
CLASSPATH=$B/bridge.jar timeout -s KILL 25 /system/bin/app_process /system/bin a9.wifi.s2.Bridge cleanup 2>/dev/null
echo 'S2_FRAMEWORK_CLEANUP disabled=yes forgotten=yes'
'''
        out=self.root(script,40,capture='framework-cleanup.log')
        if b'S2_BRIDGE cleanup=forgotten' not in out or not out.endswith(b'S2_FRAMEWORK_CLEANUP disabled=yes forgotten=yes\n'):raise Refused('framework-cleanup')
        self.framework_pending=False;self.log('framework_cleanup=passed')
    def leak_check(self,number):
        if not self.pattern_ready:raise Refused('leak-baseline')
        st=self.pattern.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1:raise Refused('leak-baseline')
        for root,dirs,files in os.walk(self.e):
            if any((Path(root)/name).is_symlink() for name in dirs+files):raise Refused('leak-symlink')
        r=subprocess.run([self.c['tools']['grep'],'-r','-a','-F','-f',str(self.pattern),'--',str(self.e),str(self.log_path)],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,close_fds=True)
        if r.returncode!=1:raise Refused('leak-hit-or-error')
        self.log('leak_check='+str(number)+' hits=0')
    def stop(self):
        # Scrub input even if run was never reached. No raw configuration/store export.
        if self.started_at is None:
            if self.root(RAM_SLOT+PRE+PRE_MPSS+"echo 'S2_STOP offline=yes'\n",45)!=b'S2_STOP offline=yes\n':raise Refused('prestart-proof')
            self.log('cleanup=stop result=not-started verified=yes');return
        script=RAM_SLOT+r'''
if [ -d /mnt/a9-wifi/trial/android-s2 ] && [ ! -L /mnt/a9-wifi/trial/android-s2 ]; then
    rm -f /mnt/a9-wifi/trial/android-s2/input
fi
'''+STOP
        if self.root(script,100)!=b'S2_STOP offline=yes\n':raise Refused('stop')
    def guard_wait(self):
        # A failed stop is never permission for an immediate MPSS-active reboot.
        # Preserve the quiet owner and wait for an offline terminal proof, or the
        # unchanged session guards' full budget plus conservative startup/reset margin.
        if self.started_at is None:return
        deadline=self.started_at+self.c['session_seconds']+630
        proof=RAM_SLOT+"case \"$(getprop vendor.a9ml.wifi.session.state)\" in ended|failed) ;; *) exit 1;; esac\n"+OFFLINE_MPSS+"echo S2_OFFLINE\n"
        self.log('stop=unproved waiting_for_existing_guards=yes')
        while time.monotonic()<deadline:
            try:
                if self.root(proof,15)==b'S2_OFFLINE\n':return
            except Exception:pass
            time.sleep(min(5,max(0,deadline-time.monotonic())))
        self.log('existing_guard_deadline=elapsed result=failed')
    def stop_boot(self):
        if self.boot is not None:
            if self.boot.poll() is None:self.boot.terminate()
            self.boot.wait(timeout=60)
            if self.boot.returncode not in (0,-signal.SIGTERM):raise Refused('boot-owner')
            end=time.monotonic()+15
            while True:
                alive=False
                for path in Path('/proc').glob('[0-9]*/stat'):
                    try:
                        fields=path.read_text().rsplit(') ',1)[1].split()
                        if int(fields[2])==self.boot.pid and fields[0] not in ('Z','X','x'):alive=True
                    except (OSError,ValueError,IndexError):continue
                if not alive:break
                if time.monotonic()>=end:raise Refused('boot-writers')
                time.sleep(.1)
        self.boot_writers_done=True
    def restore(self):
        # Explicit paths prevent the missing default a9-android-boot.img failure.
        self.call(self.boot_args()+['--restore'],timeout=1800,log='restore.log')
    def finish(self):
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,signal.SIG_IGN)
        if self.boot_attempted:
            if self.framework_pending:
                self.phase='framework-cleanup'
                try:self.framework_cleanup()
                except Exception:self.failed=True;self.log('framework_cleanup=failed')
            self.phase='stop'
            try:self.stop()
            except Exception:
                self.failed=True;self.log('cleanup=stop result=failed');self.guard_wait()
            actions=[]
            if self.pre is not None or self.started_at is not None:
                actions.append(('post',lambda:setattr(self,'post',self.live_stores(False))))
            else:self.log('cleanup=post result=not-required reason=no-pre-no-start')
            actions.append(('boot-owner',self.stop_boot))
            for label,action in actions:
                self.phase=label
                try:action()
                except Exception:self.failed=True;self.log('cleanup='+label+' result=failed')
            if self.pre is not None:
                if self.post!=self.pre:self.failed=True;self.log('stores_b=DIFFER abort=yes repair=no')
                else:self.log('stores_b=SAME count=5')
        if self.boot_attempted or self.probe_mode:
            try:self.restore();self.log('restore=passed')
            except Exception:self.failed=True;self.log('restore=failed')
            try:self.health('health-final.json');self.log('slot_a=healthy')
            except Exception:self.failed=True;self.log('slot_a=unproved')
            post_a=self.slot_a_stores('stores-a-post.json')
            self.log('stores_a=informational comparison='+('same' if post_a is not None and post_a==self.pre_a else 'different-or-unavailable'))
        if self.pattern_ready:
            try:
                if not self.boot_writers_done:raise Refused('capture-writers')
                self.leak_check(2)
            except Exception:self.failed=True;self.log('leak_check=2 result=failed')
            try:
                subprocess.run([self.c['tools']['shred'],'-u','--',str(self.pattern)],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,timeout=15,check=True,close_fds=True)
            except Exception:self.failed=True;self.log('private_cleanup=failed')
        else:self.log('leak_check=2 result=not-delivered')
        try:self.private.rmdir()
        except OSError:self.failed=True;self.log('private_directory_cleanup=failed')
        if self.probe_mode:self.log('permit=unchanged mode=probe')
        elif self.marker_exists(self.c['permit']):self.log('permit=consumed')
        elif self.marker_exists(self.delivery_fence()):self.log('permit=unused delivery=unconfirmed fence=retained')
        else:self.log('permit=unused')
        self.write('driver-exit.txt',b'1\n' if self.failed else b'0\n')
        self.log('result=failed' if self.failed else 'result=success')
        if self.lock_fd is not None:os.close(self.lock_fd);self.lock_fd=None
    def run(self):
        self.pre_a=None
        if not self.probe_mode and self.used():raise Refused('used-or-delivery-pending')
        # No evidence/private/permit mutation or phone operation when lock is held elsewhere.
        self.acquire_lock()
        if not self.probe_mode and self.used():
            os.close(self.lock_fd);self.lock_fd=None
            raise Refused('used-or-delivery-pending')
        os.umask(0o077)
        try:self.allocate()
        except BaseException:
            if self.lock_fd is not None:os.close(self.lock_fd);self.lock_fd=None
            raise
        try:
            self.log('mode=probe' if self.probe_mode else 'mode=run')
            self.setup_env();self.phase='health';self.health('health-start.json');self.pre_a=self.slot_a_stores('stores-a-pre.json')
            self.phase='boot';self.start_boot()
            self.phase='root-ready';self.wait_root()
            self.phase='store-pre';self.pre=self.live_stores(True);self.log('stores_b_pre=complete count=5')
            self.phase='install';self.install()
            self.phase='start';self.start_session()
            self.phase='logd';self.stop_logd()
            self.phase='probe';self.framework_pending=True;self.worker('probe',80)
            if self.probe_mode:self.log('probe=passed credential_access=no permit=unchanged')
            else:
                self.phase='delivery'
                try:self.deliver()
                finally:
                    if self.pattern_ready:self.leak_check(1)
                self.phase='network';self.worker('run',450);self.framework_pending=False
        except BaseException:
            self.failed=True;self.log('phase='+self.phase+' result=failed')
        finally:self.finish()
        return int(self.failed)

def main():
    try:
        p=argparse.ArgumentParser(description=__doc__);p.add_argument('--release',required=True);p.add_argument('--seal',required=True)
        group=p.add_mutually_exclusive_group(required=True);group.add_argument('--verify',action='store_true');group.add_argument('--probe',action='store_true');group.add_argument('--run',choices=['1'])
        a=p.parse_args();config=verify(a.release,a.seal)
        if a.verify:print('S2_DRIVER seal=verified phone_contact=no token_read=no');return 0
        runner=Driver(config,probe=a.probe)
        if not a.probe and runner.used():raise Refused('used-or-delivery-pending')
        root=Path(config['probes_root'] if a.probe else config['runs_root'])
        if __import__('shutil').disk_usage(root.parent).free<15*1024**3:raise Refused('space')
        config['execution_root']=str(root)
        def interrupted(signum,frame):raise Refused('signal')
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
        return runner.run()
    except BaseException:
        print('S2_DRIVER result=refused',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
