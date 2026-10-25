#!/usr/bin/env python3
"""Offline captured-grey depth replay, C engine/CLI versus actual stock code.

Run inside nix-shell -p gcc 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])':
  python3 -I scripts/mainline/eink/test/depth-replay.py COMPOSER RUN_ANDROID OUTDIR
OUTDIR must be outside this git checkout; receives derived image/level data.
No device calls. The captures are snapshots, NOT the missing full update history.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
EINK = HERE.parent
MAINLINE = EINK.parent
W, H, N = 1648, 824, 1648 * 824


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True)


def digest(b):
    return hashlib.sha256(b).hexdigest()


def assert_equal(a, b, message):
    if a != b:
        raise AssertionError(message)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('composer', type=Path)
    p.add_argument('android', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    out = args.output.resolve()
    checkout = MAINLINE.parents[1]
    if out == checkout or checkout in out.parents:
        p.error('output directory must be outside git checkout (derived vendor/image data)')
    out.mkdir(parents=True, exist_ok=True)
    composer = args.composer.resolve()
    binary = composer.read_bytes()
    core = [MAINLINE / 'tcon/a9tcon.c', MAINLINE / 'tcon/a9tcon-load.c']
    engine = EINK / 'driver/a9_eink_engine.c'
    flags = ['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-I' + str(EINK / 'driver'),
             '-I' + str(MAINLINE / 'tcon')]
    for name, source, engine_needed in [('cli', MAINLINE / 'tcon/a9tcon-cli.c', False),
                                       ('engine', HERE / 'a9e-test.c', True),
                                       ('extract', EINK / 'tools/a9wf-extract.c', True)]:
        subprocess.run(flags + ['-o', str(out / name), str(source)] + list(map(str, core)) +
                       ([str(engine)] if engine_needed else []), check=True)
    run(out / 'extract', composer, out / 'wf.bin')
    sys.path.insert(0, str(MAINLINE))
    oracle = load_module('a9_depth_oracle', MAINLINE / 'tcon-oracle.py')
    stock = oracle.Stock(binary)
    template = out / 'template.raw'
    template.write_bytes(stock.template)
    g2, g16 = stock.builders(30, 30, 0)
    assert_equal(g16, bytes(range(256)), 'stock Clear g16 is not identity at contrast 0')
    assert_equal(g2[27], 0, 'black-threshold g2 endpoint')
    assert_equal(g16[27] >> 4, 1, 'Clear grey 27 maps to level 1')
    _, high_contrast = stock.builders(30, 30, 100)
    assert_equal(high_contrast[27] >> 4, 0, 'contrast 100 maps grey 27 to level 0')
    stock.builders(30, 30, 0)
    captures = sorted(args.android.glob('compare-*/driver-grey.png'))
    if not captures:
        p.error('no compare-*/driver-grey.png captures')
    records, old = [], bytes([15]) * N
    for index, capture in enumerate(captures):
        image = Image.open(capture).convert('L')
        assert_equal(image.size, (W, H), 'driver-grey must use TCON landscape coordinates')
        grey = image.tobytes()
        raw_file = capture.with_suffix('.bin')
        raw_grey = raw_file.read_bytes()
        assert_equal(len(raw_grey), N, 'raw driver grey has wrong byte length')
        assert_equal(raw_grey, grey, 'driver-grey.bin / PNG pixel bytes differ')
        screen = np.asarray(Image.open(capture.parent / 'screencap.png').convert('RGB'))
        assert_equal(tuple(screen.shape), (W, H, 3), 'screencap must be portrait')
        colors, color_counts = np.unique(screen.reshape(-1, 3), axis=0, return_counts=True)
        color_order = np.argsort(color_counts)[::-1][:5]
        dominant_rgb = [{'rgb': [int(v) for v in colors[i]],
                         'hex': '#' + ''.join(f'{int(v):02x}' for v in colors[i]),
                         'pixels': int(color_counts[i]),
                         'stock_luma': sum(k * int(v) for k, v in zip((76, 150, 30), colors[i])) >> 8}
                        for i in color_order]
        wide = screen.astype(np.uint32)
        screen_grey = ((76 * wide[:, :, 0] + 150 * wide[:, :, 1] + 30 * wide[:, :, 2]) >> 8).astype(np.uint8)
        screen_landscape = np.ascontiguousarray(np.rot90(screen_grey, 1)).tobytes()
        ref_array = np.frombuffer(screen_landscape, dtype=np.uint8).astype(np.int16)
        driver_array = np.frombuffer(raw_grey, dtype=np.uint8).astype(np.int16)
        screen_different = int(np.count_nonzero(ref_array != driver_array))
        screen_mean_error = float(np.abs(ref_array - driver_array).mean())
        # These files were sampled separately. Preserve exact differences rather
        # than treating a displayed, rounded '100%' as atomic capture equality.

        grey_file, old_file = out / f'grey-{index}.raw', out / f'old-{index}.raw'
        grey_file.write_bytes(grey)
        old_file.write_bytes(old)
        status = (capture.parent / 'eink-status.txt').read_text()
        match = re.search(r'bin (\d+) t1 (\d+) t2 (\d+)', status)
        assert match, 'capture lacks temperature readings'
        expected_bin, t1, t2 = map(int, match.groups())
        for clear in (0, 1):
            # Full snapshot replay: intervening images are unavailable. State its
            # assumed old image explicitly; never describe this as live history.
            prefix = f'{capture.parent.name}-clear{clear}'
            common = ['--grey', grey_file, '--old', old_file, '--mode', '0x203',
                      '--pre', '0x203', '--clear', str(clear), '--t1', str(t1),
                      '--t2', str(t2), '--template', template]
            engine_levels, cli_levels = out / f'{prefix}-engine.lv', out / f'{prefix}-cli.lv'
            eng = run(out / 'engine', out / 'wf.bin', 'update', *common, '--levels', engine_levels)
            cli = run(out / 'cli', composer, *common, '--levels', cli_levels)
            assert_equal(eng.splitlines()[-1], cli.splitlines()[-1], 'engine/CLI frame digest differs')
            assert_equal(engine_levels.read_bytes(), cli_levels.read_bytes(), 'engine/CLI levels differ')
            # Invoke stock's conditioning and final-table code directly. Clear=1
            # request also has a transition pass; the CLI/engine check above
            # compares that complete pass. Here stock verifies the final table.
            st = stock.prepare(np.frombuffer(old, dtype=np.uint8),
                               np.frombuffer(grey, dtype=np.uint8), 0x203, clear, t1, t2)
            levels = engine_levels.read_bytes()
            assert_equal(st['levels'].tobytes(), levels, 'stock/engine per-pixel target levels differ')
            assert_equal(st['bin'], expected_bin, 'stock temperature-bin differs from capture')
            assert_equal(st['type'], 2 if clear else 3, 'wrong final waveform type')
            stock.uc.mem_write(oracle.G_COUNTER, bytes(4))
            hs, frames = hashlib.sha256(), 0
            while True:
                more, frame = stock.lookup(st['type'])
                hs.update(frame)
                frames += 1
                if not more:
                    assert_equal(frame, stock.template, 'stock final frame is not neutral')
                    break
                assert frames < 200
            assert_equal(frames, 30, 'bin-10 Clear table was not 30 frames')
            if not clear:
                assert_equal(f'frames {frames} sha256 {hs.hexdigest()}', eng.splitlines()[-1],
                             'actual stock / engine steady-frame digest differs')
            # Independently replay the complete request builder + dispatcher,
            # including the clear transition, in actual stock instructions.
            stock.uc.mem_write(oracle.OLD, old)
            request = stock.build_request(0x203, 0x203, clear)
            stock.prepare(request['old'], np.frombuffer(grey, dtype=np.uint8),
                          request['mode'], request['clear'], t1, t2, steps=None)
            stock.uc.mem_write(oracle.G_COUNTER, bytes(4))
            stock.uc.mem_write(oracle.G_STEP, bytes(4))
            full_hash, full_frames = hashlib.sha256(), 0
            while True:
                more, frame = stock.dispatch()
                full_hash.update(frame)
                full_frames += 1
                if not more:
                    assert_equal(frame, stock.template, 'stock dispatch did not finish neutral')
                    break
                assert full_frames < 200
            assert_equal(f'frames {full_frames} sha256 {full_hash.hexdigest()}',
                         eng.splitlines()[-1], 'actual stock complete-dispatch / engine digest differs')
            background = [(g, count) for g, count in Counter(grey).most_common(5)]
            records.append({'capture': capture.parent.name, 'clear': clear,
                            'old_assumption': 'white' if index == 0 else 'previous captured snapshot',
                            'temperature': [t1, t2], 'bin': expected_bin,
                            'target_level_histogram': dict(sorted(Counter(levels).items())),
                            'dominant_grey': background, 'dominant_screencap_rgb': dominant_rgb,
                            'raw_grey_sha256': digest(raw_grey),
                            'raw_matches_driver_png': True,
                            'screencap_grey_different_pixels': screen_different,
                            'screencap_grey_exact_percent': 100 * (N - screen_different) / N,
                            'screencap_grey_mean_absolute_difference': screen_mean_error,
                            'endpoint_scope': 'full captured-image target, not unknown historical outside-partial state',
                            'expected_levels_file': str(engine_levels),
                            'levels_sha256': digest(levels),
                            'engine_cli_complete_frames': eng.splitlines()[-1],
                            'stock_final_pass_frames': frames, 'stock_final_pass_sha256': hs.hexdigest(),
                            'stock_complete_frames': full_frames, 'stock_complete_sha256': full_hash.hexdigest()})
        old = bytes(g >> 4 for g in grey)
    # Aggregate the actual captured sequence metadata, not invented grey frames.
    text = (args.android / 'eink/hold/latest-kmsg.txt').read_text()
    pattern = (r'update done: mode (\S+) pre (\S+) clear (\d+) steps (\d+) bin (\d+) '
               r'init (\d+) rect (\S+) sent (\d+) late_drive \+(\d+)')
    updates = re.findall(pattern, text)
    assert updates, 'no completed-update metadata'
    assert all(int(row[8]) == 0 for row in updates), 'captured drive lateness'
    assert all(int(row[7]) == (123 if int(row[5]) else 30) for row in updates), 'unexpected frame counts'
    # Firmware-derived summaries only: do not commit extracted vendor tables.
    b = (out / 'wf.bin').read_bytes()[80:]
    offset, rs = 51 + 1024 + 14 * 120, 106
    phases = []
    for name, base in [('type3', offset), ('type2', offset + 14 * 257 * rs)]:
        start = base + 10 * 257 * rs
        length = b[start]
        assert_equal(length, 30, 'firmware phase count')
        for before, after in [(15, 0), (15, 1), (1, 1), (1, 0)]:
            row = start + (16 * before + after) * rs
            codes = b[row + 1:row + length + 1]
            assert_equal(codes[-1], 0, 'waveform final column not neutral')
            phases.append({'type': name, 'bin': 10, 'old': before, 'new': after,
                           'frames': length, 'code_counts': dict(sorted(Counter(codes).items()))})
    result = {'scope': 'two captured snapshots; not complete run history (intermediate grey missing)',
              'composer_sha256': digest(binary), 'captures': records,
              'captured_completed_updates': len(updates),
              'sequence_bin_counts': dict(Counter(int(row[4]) for row in updates)),
              'level_rule_clear_default': 'target = grey >> 4; grey27=1, grey0=0, grey255=15',
              'phases': phases, 'checks': 'PASS'}
    (out / 'depth-replay.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
