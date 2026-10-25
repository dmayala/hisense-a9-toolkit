#!/usr/bin/env python3
"""Stage explicit AIDL engine ranges and restrained A9 speaker volume curves.

Inputs are the existing legacy volume files. No phone access or audio playback.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

# Public SDK stream ranges (AudioService may override them through ro.config.*).
# Keep the stream names used by AOSP's default phone product strategies.
RANGES = {
    'VOICE_CALL': (1, 5), 'SYSTEM': (0, 7), 'RING': (0, 7), 'MUSIC': (0, 15),
    'ALARM': (1, 7), 'NOTIFICATION': (0, 7), 'BLUETOOTH_SCO': (0, 15),
    'ENFORCED_AUDIBLE': (0, 7), 'DTMF': (0, 15), 'TTS': (0, 15),
    'ACCESSIBILITY': (1, 15), 'ASSISTANT': (0, 15),
    'REROUTING': (0, 100), 'PATCH': (0, 100),
}
SPEAKER_STREAMS = {'MUSIC'}
# Millibels at normalized curve indexes. First audible UI steps are ~-34/-31dB;
# FIX2 uses stock QRD RX2 gain75 (-9dB); these digital media curves are unchanged.
SPEAKER_POINTS = [(0, -9600), (1, -3600), (33, -2400), (66, -1200), (100, 0)]


def stage(source_etc, dest_etc):
    source_etc, dest_etc = Path(source_etc), Path(dest_etc)
    text = (source_etc/'audio_policy_volumes.xml').read_text()
    volumes = ET.fromstring(text)
    tables_text = (source_etc/'default_volume_tables.xml').read_text()
    tables = ET.fromstring(tables_text)
    refs = {r.get('name'): r for r in tables.findall('reference')}
    groups = {}
    for volume in volumes.findall('volume'):
        stream = volume.get('stream')
        short = stream.removeprefix('AUDIO_STREAM_')
        if short not in RANGES:
            raise ValueError('unmapped stream range: '+stream)
        if short in SPEAKER_STREAMS and volume.get('deviceCategory') == 'DEVICE_CATEGORY_SPEAKER':
            volume.attrib.pop('ref', None)
            for point in list(volume): volume.remove(point)
            for index, mb in SPEAKER_POINTS: ET.SubElement(volume, 'point').text = f'{index},{mb}'
        groups.setdefault(stream, []).append(volume)
    assert set(groups) == {'AUDIO_STREAM_'+n for n in RANGES}
    engine = ET.Element('configuration', version='1.0')
    engine.append(ET.Comment('A9: default phone strategies; explicit native stream ranges.'))
    engine_groups = ET.SubElement(engine, 'volumeGroups')
    for stream, curves in groups.items():
        group = ET.SubElement(engine_groups, 'volumeGroup')
        ET.SubElement(group, 'name').text = stream
        low, high = RANGES[stream.removeprefix('AUDIO_STREAM_')]
        ET.SubElement(group, 'indexMin').text = str(low)
        ET.SubElement(group, 'indexMax').text = str(high)
        seen = set()
        for curve in curves:
            category = curve.get('deviceCategory')
            assert category not in seen, (stream, category)
            seen.add(category)
            node = ET.SubElement(group, 'volume', deviceCategory=category)
            points = refs[curve.get('ref')].findall('point') if curve.get('ref') else curve.findall('point')
            assert points
            for p in points: node.append(deepcopy(p))
    dest_etc.mkdir(parents=True, exist_ok=True)
    ET.indent(volumes)
    # Preserve the input's AOSP license/comment header outside its root element.
    prefix = text[:text.index('<volumes>')]
    (dest_etc/'audio_policy_volumes.xml').write_text(prefix+ET.tostring(volumes, encoding='unicode')+'\n')
    (dest_etc/'default_volume_tables.xml').write_text(tables_text)
    ET.indent(engine)
    (dest_etc/'audio_policy_engine_configuration.xml').write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(engine, encoding='unicode')+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-etc', type=Path, required=True)
    parser.add_argument('--dest-etc', type=Path, required=True)
    args = parser.parse_args()
    stage(args.source_etc, args.dest_etc)
