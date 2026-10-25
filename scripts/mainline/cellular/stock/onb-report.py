#!/usr/bin/env python3
"""Allowlisted hook records and verdicts only. No device tools or raw dumps."""
from pathlib import Path
import re,sys,time
TAGS=('qcrild','qcrild2','imsqmidaemon','imsdatadaemon','ims_rtp_daemon','imsrcsd','audioserver','audio_hal')
STOCK=TAGS[:6]
CRASH=('qcrild','imsqmidaemon','imsdatadaemon','ims_rtp_daemon','imsrcsd','audioserver','audio_hal')
PROP={
 'qtibus_directory':('0','1','unknown'),
 'qtibus_socket':('0','1','unknown'),
 'boot':('0','1','unknown'),
 'owner':('serving','awaiting-s2-audit','armed','starting','stopped','failed','ended','ending','unknown'),
 'stock_ready':('0','1','unknown'),
 'stock_state':('idle','requested','preparing','prepared','starting','stopped','failed','unknown'),
 'stock_phase':('idle','gates','modules','sources','copying','database','labeling','verifying','metadata','complete','unknown'),
 'stock_count':tuple(str(n) for n in range(135))+('unknown',),
 'cellular':('0','1','unknown'),
 'owner_ready':('0','1','unknown'),
 'prep_service':('running','stopped','restarting','unknown'),
 'modules_service':('running','stopped','restarting','unknown'),
 'netmgr':('blocked-kernel-abi','unknown'),
}
SIM=('ABSENT','READY','NOT_READY','PIN_REQUIRED','PUK_REQUIRED','UNKNOWN','CARD_IO_ERROR','CARD_RESTRICTED','PERM_DISABLED','LOADED','NETWORK_LOCKED','PRESENT','DISABLED')
FW=('radio_slot1','radio_slot2','ims_slot1','ims_slot2','binder_radio_count','audio_module','wifi_enabled','sim_absent_evidence','sim_other_evidence','audio_output_threads','wifi_enable_request','registry_phone0','registry_phone1')
def integer(s):return bool(re.fullmatch(r'[0-9]{1,20}',s))
def valid(line,kind=None):
 f=line.strip().split()
 if not f:return False
 if len(f)==3 and f[0]=='NATIVE_EXIT':return integer(f[1]) and (integer(f[2]) or f[2]=='unknown')
 if kind in (None,'native'):
  if len(f)==3 and f[0]=='PROP':return f[1] in PROP and f[2] in PROP[f[1]]
  if len(f)==6 and f[0]=='PID':return f[1] in TAGS and all(integer(x) for x in f[2:5]) and f[5] in ('running','stopped','restarting','unknown')
  if len(f)==4 and f[0]=='CRASH':return f[1] in CRASH and integer(f[2]) and f[3] in ('0','1')
  if len(f)==3 and f[0]=='GSM':return f[1] in ('1','2') and f[2] in SIM
  if len(f)==3 and f[0]=='ACTION':return f[1] in ('owner_start','cellular_optin','clients_stopped') and f[2] in ('0','1','2')
  if len(f)==3 and f[:2]==['RC','txpower']:return integer(f[2]) and int(f[2])<=255
 if kind in (None,'framework') and len(f)==3 and f[0]=='FW' and f[1] in ('sim_slot0','sim_slot1'):
  return f[2] in SIM
 if kind in (None,'framework') and len(f)==3 and f[0]=='FW' and f[1] in FW:
  return integer(f[2]) or (f[1]=='wifi_enabled' and f[2]=='-1')
 return False

def load(path):
 try:lines=Path(path).read_text().splitlines()
 except OSError:return {}
 out={}
 for l in lines:
  if not valid(l):continue
  f=l.split();key=tuple(f[:2]);v=tuple(f[2:])
  if key in out: # Ambiguous duplicate status is never treated as success.
   out[key]=()
  else:out[key]=v
 return out

def one(d,kind,key,default='unknown'):
 v=d.get((kind,key),());return v[0] if len(v)==1 else default

def alive(d,tag):
 p=d.get(('PID',tag),())
 return len(p)==4 and int(p[0])>0 and int(p[1])>0 and p[2]=='1' and p[3]=='running'

def summary(folder):
 folder=Path(folder)
 starts=load(folder/'start.native.tsv');ends=load(folder/'end.native.tsv')
 fs=load(folder/'start.framework.tsv');fe=load(folder/'end.framework.tsv')
 aus=load(folder/'start.audio.tsv');aue=load(folder/'end.audio.tsv')
 monitors=[load(p) for p in sorted(folder.glob('monitor-*.tsv'))]
 snapshots=[starts,*monitors,ends]
 ready=load(folder/'readiness.tsv');opt=load(folder/'opt-in.tsv')
 # Diagnostic fallback is only for independent observed states, never 180s or
 # process stability. Prefer the last collection before cellular opt-out.
 diagnostic_native=load(folder/'final.native.tsv') or ends or load(folder/'preparation.native.tsv') or starts
 diagnostic_fw=load(folder/'final.framework.tsv') or fe or load(folder/'preparation.framework.tsv') or fs
 diagnostic_audio=load(folder/'final.audio.tsv') or aue or load(folder/'preparation.audio.tsv') or aus
 full_window=bool(starts and ends and monitors)
 try:stage=(folder/'hook-stage').read_text().strip()
 except OSError:stage='unknown'
 if stage not in ('initializing','owner_request','owner_wait','opt_in','preparation_diagnostics','stock_wait','client_warmup','observation','complete'):stage='unknown'

 try:elapsed=float((folder/'window.end').read_text())-float((folder/'window.start').read_text())
 except (OSError,ValueError):elapsed=0
 try:rc=int((folder/'hook-status').read_text())
 except (OSError,ValueError):rc=1
 verdicts=[]
 def emit(name,ok,detail=''):
  verdicts.append(bool(ok));print(('PASS' if ok else 'FAIL')+' '+name+(' '+detail if detail else ''))
 emit('hook_completion',rc==0,f'rc={rc} stage={stage}')
 transport_ok=False
 try:
  rows=(folder/'transport-status.tsv').read_text().splitlines()
  transport_ok=bool(rows)
  for row in rows:
   parts=row.split();transport_ok=transport_ok and len(parts)==4 and parts[0] in ('start-owner','wait-owner','wait-stock','opt-in','monitor','snapshot','cleanup','framework','audio') and parts[1:]==['0','0','0']
 except OSError:pass
 emit('hook_transport',transport_ok)
 emit('boot_completed',one(diagnostic_native,'PROP','boot')=='1' and (not full_window or all(one(d,'PROP','boot')=='1' for d in snapshots)), 'observed='+one(diagnostic_native,'PROP','boot'))
 emit('mpss_owner_serving',one(diagnostic_native,'PROP','owner')=='serving' and (not full_window or all(one(d,'PROP','owner')=='serving' for d in snapshots)), 'observed='+one(diagnostic_native,'PROP','owner'))
 emit('stock_prepared',one(opt,'ACTION','cellular_optin')=='1' and one(opt,'RC','txpower')=='0' and one(ready,'PROP','stock_ready')=='1' and one(diagnostic_native,'PROP','stock_ready')=='1' and one(diagnostic_native,'PROP','stock_state') in ('prepared','starting') and (all(one(d,'PROP','stock_ready')=='1' and one(d,'PROP','stock_state') in ('prepared','starting') for d in snapshots) if full_window else (one(diagnostic_native,'PROP','stock_phase')=='complete' and one(diagnostic_native,'PROP','stock_count')=='134')), 'ready='+one(diagnostic_native,'PROP','stock_ready')+' state='+one(diagnostic_native,'PROP','stock_state')+' phase='+one(diagnostic_native,'PROP','stock_phase')+' copied='+one(diagnostic_native,'PROP','stock_count')+' prep='+one(diagnostic_native,'PROP','prep_service')+' modules='+one(diagnostic_native,'PROP','modules_service'))
 emit('observation_180s',elapsed>=180 and len(monitors)>=1,f'elapsed_s={max(elapsed,0):.1f}')
 for tag in STOCK:
  a=starts.get(('PID',tag),('0','0','0','unknown'));b=ends.get(('PID',tag),('0','0','0','unknown'))
  ok=elapsed>=180 and len(monitors)>=1 and alive(starts,tag) and all(alive(d,tag) and d.get(('PID',tag))==a for d in snapshots)
  emit('stable_'+tag,ok,f'start_pid={a[0] if a else "0"} end_pid={b[0] if b else "0"} start_state={a[3] if len(a)==4 else "unknown"} end_state={b[3] if len(b)==4 else "unknown"}'+(' ipc_dir='+one(ends,'PROP','qtibus_directory')+' ipc_socket='+one(ends,'PROP','qtibus_socket') if tag=='qcrild' else ''))
 for key in ('radio_slot1','radio_slot2','ims_slot1','ims_slot2'):
  emit(('IRadio_1_4_' if key.startswith('radio') else 'IImsRadio_')+key.split('_')[-1],one(fs,'FW',key)=='1' and one(fe,'FW',key)=='1')
 emit('gsm_sim_ABSENT',all(one(d,'GSM',str(i))=='ABSENT' for d in (starts,ends) for i in (1,2)), 'start='+','.join(one(starts,'GSM',str(i)) for i in (1,2))+' end='+','.join(one(ends,'GSM',str(i)) for i in (1,2)))
 def sim_absent(d):
  return all(one(d,'FW','sim_slot'+str(i))=='ABSENT' for i in (0,1))
 emit('telephony_sim_ABSENT',sim_absent(fs) and sim_absent(fe), 'source=isub start='+','.join(one(fs,'FW','sim_slot'+str(i)) for i in (0,1))+' end='+','.join(one(fe,'FW','sim_slot'+str(i)) for i in (0,1)))
 deltas=[];crash_ok=True
 for tag in CRASH:
  a=starts.get(('CRASH',tag),());b=ends.get(('CRASH',tag),())
  if len(a)!=2 or len(b)!=2 or a[1]!='1' or b[1]!='1':crash_ok=False;deltas.append(tag+'=unknown');continue
  delta=int(b[0])-int(a[0]);crash_ok=crash_ok and delta==0
  deltas.append(f'{tag}={a[0]}:{b[0]}')
 emit('crash_buffer_counts',crash_ok,'start:end '+','.join(deltas))
 emit('wifi_enabled',one(load(folder/'wifi-request.tsv'),'FW','wifi_enable_request')=='1' and one(diagnostic_fw,'FW','wifi_enabled')=='1' and (not full_window or (one(fs,'FW','wifi_enabled')=='1' and one(fe,'FW','wifi_enabled')=='1')), 'observed='+one(diagnostic_fw,'FW','wifi_enabled'))
 audio_ok=one(diagnostic_fw,'FW','audio_module')=='1' and all(alive(diagnostic_native,t) for t in ('audioserver','audio_hal'))
 if full_window:
  audio_ok=audio_ok and one(fs,'FW','audio_module')=='1' and one(fe,'FW','audio_module')=='1'
  for tag in ('audioserver','audio_hal'):
   audio_ok=audio_ok and alive(starts,tag) and all(alive(d,tag) and d.get(('PID',tag))==starts.get(('PID',tag)) for d in snapshots)
 outputs=[one(d,'FW','audio_output_threads','0') for d in (aus,aue)] if full_window else [one(diagnostic_audio,'FW','audio_output_threads','0')]
 emit('audio_output_open',audio_ok and all(int(x)>0 for x in outputs),'mode='+('window' if full_window else 'diagnostic')+' AudioFlinger_outputs='+','.join(outputs))
 emit('stock_clients_stopped_before_return',one(load(folder/'cleanup.tsv'),'ACTION','clients_stopped')=='1')
 return 0 if all(verdicts) else 1

def main():
 if sys.argv[1]=='filter':
  for line in sys.stdin:
   failure=re.fullmatch(r'A9_ONB_NATIVE_EXIT rc=([0-9]{1,3}) line=([0-9]{1,8}|unknown)\s*',line)
   if failure:
    print('NATIVE_EXIT',failure[1],failure[2]);continue
   if valid(line,sys.argv[2]):print(' '.join(line.split()))
  return 0
 if sys.argv[1]=='stamp':print(f'{time.monotonic():.9f}');return 0
 if sys.argv[1]=='processes-ready':return 0 if all(alive(load(sys.argv[2]),t) for t in STOCK) else 1
 if sys.argv[1]=='summary':return summary(sys.argv[2])
 return 2
if __name__=='__main__':sys.exit(main())
