#!/usr/bin/env python3
"""Host-only JPEG acceptance metrics or stride-aware MIPI RAW10 statistics.

Use a stationary, evenly lit neutral grey/white card in the selected ROI for the
JPEG colour criterion. Coloured rooms and occluded lenses are not neutral tests.
JPEG needs Pillow; RAW10 uses only the Python standard library.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('image', type=Path)
p.add_argument('--raw', action='store_true')
p.add_argument('--width', type=int, default=2104)
p.add_argument('--height', type=int, default=1560)
p.add_argument('--stride', type=int, default=2632)
p.add_argument('--roi', default='0.25,0.25,0.75,0.75', help='fractional x0,y0,x1,y1; default central half')
a = p.parse_args()
roi = list(map(float, a.roi.split(',')))
if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
    p.error('ROI must be x0,y0,x1,y1 fractions within0..1')
if a.raw:
    if a.width <= 0 or a.width % 4 or a.height <= 0 or a.stride < a.width * 5 // 4:
        p.error('invalid packed RAW10 width/height/stride')
    data = a.image.read_bytes()
    if len(data) != a.stride * a.height:
        p.error('file length differs from stride*height; use the camcap printed values')
    box = [int(roi[0] * a.width) & ~1, int(roi[1] * a.height) & ~1,
           int(roi[2] * a.width) & ~1, int(roi[3] * a.height) & ~1]
    if box[0] >= box[2] or box[1] >= box[3]: p.error('ROI is empty at this resolution')
    channels = {'R': Counter(), 'G': Counter(), 'B': Counter()}
    for y in range(box[1], box[3]):
        for x in range(box[0] & ~3, (box[2] + 3) & ~3, 4):
            pos = y * a.stride + x // 4 * 5
            low = data[pos + 4]
            for phase in range(4):
                xx = x + phase
                if not box[0] <= xx < box[2]: continue
                value = data[pos + phase] * 4 + ((low >> (phase * 2)) & 3)
                # S5K3L6 default GRBG (no flips), including original sensor parity.
                channel = 'G' if (y & 1) == (xx & 1) else ('R' if not y & 1 else 'B')
                channels[channel][value] += 1
    hist = sum(channels.values(), Counter()); count = sum(hist.values())
    def percentile(percent):
        seen = 0
        for value, n in sorted(hist.items()):
            seen += n
            if seen >= count * percent / 100: return value
    result = {'format': 'GRBG10P', 'roi_pixels': box, 'pixels': count,
              'mean': sum(v * n for v,n in hist.items()) / count,
              'p5': percentile(5), 'p50': percentile(50), 'p95': percentile(95),
              'black64_fraction': sum(n for v,n in hist.items() if v <= 68) / count,
              'clipped_fraction': sum(n for v,n in hist.items() if v >= 1020) / count,
              'channel_means': {c: sum(v*n for v,n in h.items()) / sum(h.values()) for c,h in channels.items()}}
    print(json.dumps(result, indent=2))
else:
    from PIL import Image, ImageStat
    with Image.open(a.image) as source:
        im = source.convert('RGB')
        box = [int(roi[0]*im.width), int(roi[1]*im.height), int(roi[2]*im.width), int(roi[3]*im.height)]
        if box[0] >= box[2] or box[1] >= box[3]: p.error('ROI is empty at this resolution')
        crop = im.crop(box); means = ImageStat.Stat(crop).mean
        y = crop.convert('L'); hist = y.histogram(); count = crop.width * crop.height
        luma = ImageStat.Stat(y).mean[0]
        ratio = max(means) / max(1, min(means))
        black = sum(hist[:5]) / count; clipped = sum(hist[251:]) / count
        passed = 60 <= luma <= 180 and ratio <= 1.25 and black < .05 and clipped < .05
        print(json.dumps({'pass': passed, 'criterion': 'neutral-card indoor ROI', 'size': im.size,
                          'roi_pixels': box, 'mean_luma': luma, 'rgb_means': means,
                          'max_min_channel_ratio': ratio, 'black_fraction': black,
                          'clipped_fraction': clipped}, indent=2))
        raise SystemExit(0 if passed else 1)
