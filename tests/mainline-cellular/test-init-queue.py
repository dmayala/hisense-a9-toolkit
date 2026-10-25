#!/usr/bin/env python3
"""Replay the observed init FIFO trap using real RC action bodies, offline.

late-init has already queued post-fs-data, zygote-start and boot. Property
changes append events; a direct start executes in the current event. This
fixture tests scheduling, not modem timing or a claim of hardware zero ANRs.
"""
from collections import deque
from pathlib import Path
import itertools,re,unittest
R=Path(__file__).resolve().parents[2]
M=R/'scripts/mainline'

def actions(text):
    result=[];current=None
    for line in text.splitlines():
        if line.startswith('on '):
            current=[line[3:],[]];result.append(current)
        elif line and not line[0].isspace() and not line.startswith('#'):current=None
        elif current is not None and line.strip() and not line.lstrip().startswith('#'):
            current[1].append(line.strip())
    return result

def replay(groups,props):
    props=dict(props);queue=deque(['post-fs-data','zygote-start','boot']);trace=[]
    all_actions=[a for g in groups for a in actions(g)]
    while queue:
        event=queue.popleft();trace.append('event '+event)
        for trigger,commands in all_actions:
            terms=trigger.split(' && ')
            if terms[0]!=event:continue
            gates=re.findall(r'property:([^=]+)=([^ ]+)', ' && '.join(terms[1:]))
            if not all(props.get(k)==v for k,v in gates):continue
            for cmd in commands:
                trace.append(cmd)
                if cmd.startswith('setprop '):
                    _,key,value=cmd.split()
                    if props.get(key)!=value:
                        props[key]=value;queue.append('property:'+key+'='+value)
    return trace

class QueueRegression(unittest.TestCase):
    def test_old_property_hop_is_after_zygote(self):
        old='''on post-fs-data
    setprop vendor.test.request 1
on property:vendor.test.request=1
    start a9ml_wifi_session
'''
        t=replay([old],{})
        self.assertLess(t.index('event zygote-start'),t.index('start a9ml_wifi_session'))

    def test_direct_owner_is_after_firmware_before_framework_for_all_import_orders(self):
        sources=[(M/p).read_text() for p in ('wifi/a9-wifi.rc','wifi/android/s2/s2-session.rc','cellular/stock/vendor/etc/init/init.a9ml-stock-radio.rc')]
        for order in itertools.permutations(sources):
            t=replay(order,{'ro.a9ml.wifi':'1','ro.a9ml.wifi.session':'1','ro.vendor.a9ml.cellular':'1'})
            self.assertEqual(t.count('start a9ml_wifi_session'),1)
            self.assertLess(t.index('exec_start a9ml_wifi_firmware'),t.index('start a9ml_wifi_session'))
            self.assertLess(t.index('start a9ml_wifi_session'),t.index('event zygote-start'))
            self.assertLess(t.index('exec_start a9ml_wifi_firmware'),t.index('exec_start a9ml-cellular-prepare'))
            self.assertLess(t.index('exec_start a9ml-cellular-prepare'),t.index('exec_start a9ml-stock-radio-prepare'))
            self.assertLess(t.index('exec_start a9ml-stock-radio-prepare'),t.index('event zygote-start'))
            self.assertEqual(t.count('exec_start a9ml-stock-radio-prepare'),1)
            self.assertNotIn('setprop vendor.a9ml.wifi.s2.start 1',t)

    def test_absent_build_optin_never_starts_owner(self):
        common=(M/'wifi/a9-wifi.rc').read_text()
        for props in ({},{'ro.a9ml.wifi':'1'},{'ro.a9ml.wifi.session':'1'}):
            trace=replay([common],props)
            self.assertNotIn('start a9ml_wifi_session',trace)
            self.assertNotIn('exec_start a9ml-stock-radio-prepare',trace)

    def test_legacy_session_has_no_second_automatic_start(self):
        regular=(M/'wifi/session/init.a9ml-wifi-session.rc').read_text()
        self.assertNotIn('property:sys.boot_completed',regular)
        common=(M/'wifi/a9-wifi.rc').read_text()
        t=replay([common,regular],{'ro.a9ml.wifi':'1','ro.a9ml.wifi.session':'1','ro.vendor.a9ml.cellular':'1'})
        self.assertEqual(t.count('start a9ml_wifi_session'),1)

    def test_incremental_packaging_refreshes_both_owner_rcs(self):
        recipe=(M/'reboot/build14.py').read_text()
        self.assertIn("'etc/init/init.a9ml-wifi.rc': MAIN/'wifi/a9-wifi.rc'",recipe)
        self.assertIn("'etc/init/init.a9ml-wifi-session.rc': MAIN/'wifi/android/s2/s2-session.rc'",recipe)

if __name__=='__main__':unittest.main(verbosity=2)
