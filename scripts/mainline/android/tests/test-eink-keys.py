#!/usr/bin/env python3
"""Compile base/Android DTBs offline and verify the active-low key pin states."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    repo = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'A9-build/mainline/linux-7.2.10')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='a9-key-dt-') as temporary:
        out = args.out or Path(temporary)
        out.mkdir(parents=True, exist_ok=True)
        includes = [repo / 'docs/mainline', repo / 'scripts/mainline/android',
                    repo / 'scripts/mainline/pmic', args.source / 'arch/arm64/boot/dts/qcom',
                    args.source / 'include']
        for variant, board in [('base', 'sm6115-hisense-hlte556n'),
                               ('android', 'sm6115-hisense-hlte556n-android')]:
            src, pre, dtb = (out / (variant + suffix) for suffix in ('.dts', '.pre.dts', '.dtb'))
            src.write_text(f'#include "{board}.dts"\n')
            cpp = ['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp']
            for p in includes:
                cpp += ['-I', str(p)]
            with pre.open('w') as f:
                subprocess.run([*cpp, str(src)], stdout=f, check=True)
            with (out / (variant + '.dtc.log')).open('w') as f:
                subprocess.run(['dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(pre)],
                               stderr=f, check=True)

            def get(node, key, kind='s', absent=False):
                r = subprocess.run(['fdtget', '-t', kind, str(dtb), node, key], capture_output=True, text=True)
                if absent:
                    assert r.returncode and 'FDT_ERR_NOTFOUND' in r.stderr, (node, key, r)
                    return None
                assert not r.returncode, (node, key, r.stderr)
                return r.stdout.strip()

            symbol = lambda label: get('/__symbols__', label)
            cells = lambda node, key: list(map(int, get(node, key, 'u').split()))
            tlmm, pmic = symbol('tlmm'), symbol('pm6125_gpios')
            e_state, v_state = symbol('eink_key_default'), symbol('vol_up_n')
            t_handle, p_handle = cells(tlmm, 'phandle')[0], cells(pmic, 'phandle')[0]
            assert cells('/gpio-keys/key-eink', 'gpios') == [t_handle, 84, 1]
            assert cells('/gpio-keys/key-volume-up', 'gpios') == [p_handle, 5, 1]
            assert cells('/gpio-keys/key-eink', 'linux,code') == [766]
            assert cells('/gpio-keys/key-volume-up', 'linux,code') == [115]
            for key in ('key-eink', 'key-volume-up'):
                assert cells('/gpio-keys/' + key, 'debounce-interval') == [15]
                assert get('/gpio-keys/' + key, 'wakeup-source') == ''
            assert get('/gpio-keys', 'pinctrl-names') == 'default'
            assert cells('/gpio-keys', 'pinctrl-0') == [cells(v_state, 'phandle')[0],
                                                      cells(e_state, 'phandle')[0]]
            for state, pin, function in [(e_state, 'gpio84', 'gpio'), (v_state, 'gpio5', 'normal')]:
                assert get(state, 'pins') == pin and get(state, 'function') == function
                for key in ('bias-pull-up', 'input-enable', 'output-disable'):
                    assert get(state, key) == ''
                for key in ('bias-pull-down', 'output-high', 'output-low', 'output-enable'):
                    get(state, key, absent=True)
            assert cells(e_state, 'drive-strength') == [2]
            assert cells(v_state, 'power-source') == [0]
            ranges = cells(tlmm, 'gpio-reserved-ranges')
            assert not any(start <= 84 < start + count for start, count in zip(ranges[::2], ranges[1::2]))
            text = subprocess.check_output(['dtc', '-q', '-I', 'dtb', '-O', 'dts', str(dtb)], text=True)
            assert len(re.findall(r'"gpio84"', text)) == 1, 'conflicting GPIO84 pin states'
            uses = []
            for match in re.finditer(r'\b(?:[\w-]+-)?gpios\s*=\s*<([^>]+)>', text):
                values = [int(v, 0) for v in match[1].split()]
                uses += [values[i:i + 3] for i in range(0, len(values), 3)]
            assert uses.count([t_handle, 84, 1]) == 1, 'GPIO84 must have exactly one GPIO consumer'
            assert sum(v[:2] == [t_handle, 84] for v in uses) == 1
            assert sum(v[:2] == [p_handle, 5] for v in uses) == 1
            print(f'PASS {variant}: GPIO84 input/up/2mA, PM6125 GPIO5 input/up/output-off; unique consumers; GPIO84 unreserved')


if __name__ == '__main__':
    main()
