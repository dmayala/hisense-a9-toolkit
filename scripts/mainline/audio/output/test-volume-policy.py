#!/usr/bin/env python3
"""Check native ranges, legacy parity, attenuation and unchanged other devices."""
import argparse
from bisect import bisect_left
import importlib.util
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('volumes', HERE/'volume-policy.py')
volumes = importlib.util.module_from_spec(spec); spec.loader.exec_module(volumes)


def points(v, refs):
    v = refs[v.get('ref')] if v.get('ref') else v
    return [tuple(map(int, p.text.split(','))) for p in v.findall('point')]


def db(curve, index, low, high):
    # AOSP VolumeCurve::volIndexToDb: 101 normalized steps, integer scaling.
    if index < low: return -96.0
    normalized = (1+curve[-1][0]-curve[0][0]) * (index-low)//(high-low)
    pos = bisect_left([p[0] for p in curve], normalized)
    if pos == len(curve): return curve[-1][1]/100
    if not pos: return curve[0][1]/100 if normalized == curve[0][0] else -96.0
    x0, y0 = curve[pos-1]; x1, y1 = curve[pos]
    return (y0+(normalized-x0)*(y1-y0)/(x1-x0))/100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-etc', type=Path, required=True)
    ap.add_argument('--result', type=Path, required=True)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='volume-test-') as tmp:
        tmp = Path(tmp); volumes.stage(args.source_etc, tmp)
        root = ET.parse(tmp/'audio_policy_engine_configuration.xml').getroot()
        groups = {g.findtext('name'): g for g in root.find('volumeGroups')}
        assert len(groups) == 14
        for key, (lo, hi) in volumes.RANGES.items():
            g = groups['AUDIO_STREAM_'+key]
            assert int(g.findtext('indexMin')) == lo >= 0
            assert int(g.findtext('indexMax')) == hi > lo
            assert len(g.findall('volume')) == 5
        print('PASS 14 explicit ranges, default phone names and 5 categories per group')
        refs = {v.get('name'): v for v in ET.parse(tmp/'default_volume_tables.xml').getroot()}
        before = {(v.get('stream'), v.get('deviceCategory')): v
                  for v in ET.parse(args.source_etc/'audio_policy_volumes.xml').getroot().findall('volume')}
        rows = []
        for v in ET.parse(tmp/'audio_policy_volumes.xml').getroot().findall('volume'):
            key = v.get('stream'), v.get('deviceCategory')
            engine = next(x for x in groups[key[0]].findall('volume') if x.get('deviceCategory') == key[1])
            assert points(v, refs) == points(engine, refs)
            if key != ('AUDIO_STREAM_MUSIC', 'DEVICE_CATEGORY_SPEAKER'):
                assert v.attrib == before[key].attrib and points(v, refs) == points(before[key], refs)
        assert (tmp/'default_volume_tables.xml').read_bytes() == (args.source_etc/'default_volume_tables.xml').read_bytes()
        print('PASS legacy/AIDL curve parity; only speaker music curve changed, tables unchanged')
        for stream in ('MUSIC', 'RING', 'NOTIFICATION'):
            lo, hi = volumes.RANGES[stream]
            key = 'AUDIO_STREAM_'+stream, 'DEVICE_CATEGORY_SPEAKER'
            old = points(before[key], refs)
            curve = points(next(v for v in groups[key[0]].findall('volume') if v.get('deviceCategory') == key[1]), refs)
            gains = [db(curve, i, lo, hi) for i in range(lo, hi+1)]
            assert all(a < b for a, b in zip(gains, gains[1:])), (stream, gains)
            assert gains[0] <= -96 and gains[-1] == 0 and max(gains) <= 0
            for i, gain in enumerate(gains, start=lo):
                rows.append({'stream': stream, 'index': i, 'software_dB': round(gain, 3),
                             'with_RX54_dB': round(gain-30, 3), 'old_software_dB': round(db(old, i, lo, hi), 3)})
        print('PASS all stream2/3/5 steps finite/monotonic, index0 mute attenuation, peak never above existing -30dB RX ceiling')
        # Refuse unknown streams rather than silently leaving a deferred range.
        text = (args.source_etc/'audio_policy_volumes.xml').read_text().replace('AUDIO_STREAM_MUSIC', 'AUDIO_STREAM_UNKNOWN')
        bad = tmp/'bad'; bad.mkdir(); (bad/'audio_policy_volumes.xml').write_text(text)
        (bad/'default_volume_tables.xml').write_bytes((tmp/'default_volume_tables.xml').read_bytes())
        try: volumes.stage(bad, tmp/'refused')
        except ValueError: pass
        else: raise AssertionError('unknown stream accepted')
        print('PASS unknown stream fails closed during generation')
        args.result.write_text(json.dumps({'gain_steps': rows, 'qualification': 'XML/native attenuation model only; no AudioService Binder calls or playback'}, indent=2)+'\n')


if __name__ == '__main__': main()
