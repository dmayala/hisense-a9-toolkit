#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path(__file__).resolve().parents[1]
r=ET.parse(p/'primary_audio_policy_configuration.a9-candidate.xml').getroot()
assert r.attrib=={'name':'primary','halVersion':'3.0'}
attached={x.text for x in r.findall('./attachedDevices/item')}
assert attached=={'Speaker','Built-In Mic'}
ports={x.attrib['tagName']:x for x in r.findall('./devicePorts/devicePort')}
assert len(ports)==6 and 'Wired Headset' not in attached
assert ports['Built-In Mic'].attrib['address']=='bottom'
for port in r.findall('./mixPorts/mixPort'):
    assert 'MMAP' not in port.attrib.get('flags','') and 'OFFLOAD' not in port.attrib.get('flags','')
    for profile in port.findall('profile'):
        assert profile.attrib['format']=='AUDIO_FORMAT_PCM_16_BIT'
        rates=set(profile.attrib['samplingRates'].split())
        assert rates<= {'16000','48000'}
        if port.attrib['role']=='sink': assert profile.attrib['channelMasks']=='AUDIO_CHANNEL_IN_MONO'
assert all('Telephony' not in str(x.attrib) and 'FM' not in str(x.attrib) for x in r.iter())
assert 'CARD_' not in (p/'primary_audio_policy_configuration.a9-candidate.xml').read_text()
print('A9_AUDIO_POLICY_CANDIDATE PASS: restricted PCM profiles, external jack, no offload/MMAP/FM/call claim')
