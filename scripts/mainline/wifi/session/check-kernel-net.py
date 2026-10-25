#!/usr/bin/env python3
"""Offline: does a kernel config carry what Android's Wi-Fi data path needs (not just the radio)?

Reads one .config (a snapshot's `config`). Prints REQUIRED/ADVISORY gaps; exit 1 on a required gap.
The list is what netd, ConnectivityService, wificond, the in-framework DHCP client and the BPF
traffic accounting use; it is a hand-kept checklist (INFERRED from AOSP behaviour), not a copy of
the GKI fragment, so a pass is necessary, not sufficient.
"""
import re
import sys

REQUIRED = '''PACKET UNIX INET IPV6 NETFILTER NF_CONNTRACK NF_NAT IP_NF_IPTABLES IP_NF_FILTER IP_NF_MANGLE IP_NF_NAT
IP6_NF_IPTABLES IP6_NF_FILTER IP6_NF_MANGLE NETFILTER_XT_MATCH_OWNER NETFILTER_XT_MATCH_MARK NETFILTER_XT_TARGET_MARK
NETFILTER_XT_MATCH_CONNTRACK NETFILTER_XT_MATCH_QUOTA NETFILTER_XT_MATCH_STATE NETFILTER_XT_TARGET_CLASSIFY
BPF_SYSCALL BPF_JIT CGROUP_BPF NET_CLS_BPF NET_ACT_BPF NET_SCH_INGRESS NET_CLS_ACT INET_DIAG INET_UDP_DIAG
IP_MULTIPLE_TABLES IPV6_MULTIPLE_TABLES IP_ADVANCED_ROUTER CFG80211 MAC80211 RFKILL WLAN WLAN_VENDOR_ATH
CFG80211_REQUIRE_SIGNED_REGDB CFG80211_USE_KERNEL_REGDB_KEYS'''.split()
MODULE_OK = {'ATH10K', 'ATH10K_SNOC'}
ADVISORY = {'TUN': 'VPN / tethering helpers', 'NET_SCH_FQ_CODEL': 'default qdisc (pfifo_fast works)',
            'NETFILTER_XT_MATCH_UID': 'not used by current netd (cgroup-bpf)', 'CFG80211_WEXT': 'legacy; wificond uses nl80211',
            'NL80211_TESTMODE': 'factory test only', 'XFRM_USER': 'IPsec VPN only', 'IFB': 'ingress shaping only'}


def main(path):
    conf = {}
    for line in open(path):
        m = re.match(r'CONFIG_(\w+)=(.*)', line.strip())
        if m: conf[m.group(1)] = m.group(2)
    gaps = [o for o in REQUIRED if conf.get(o) not in ('y',)]
    for o in sorted(MODULE_OK):
        if conf.get(o) not in ('y', 'm'): gaps.append(o)
    for o in gaps: print('REQUIRED missing:', o)
    for o, why in ADVISORY.items():
        if conf.get(o) not in ('y', 'm'): print('ADVISORY absent: %s (%s)' % (o, why))
    print('kernel net checklist: %d required options checked, %d gaps' % (len(REQUIRED) + len(MODULE_OK), len(gaps)))
    return 1 if gaps else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
