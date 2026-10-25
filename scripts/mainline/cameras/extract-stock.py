#!/usr/bin/env python3
"""Decode the A9's two CamX Parameter Parser V2 sensor modules, offline.

Inputs are extracted read-only from stock vendor. Outputs (including register
tables) stay in the local build directory; do not commit vendor payloads.
This decoder describes these two exact module layouts, not arbitrary Chromatix.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


class Module:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        assert self.data.startswith(b'QTI Chromatix Header')
        self.base, self.length = struct.unpack_from('<2Q', self.data, 0xc0)
        table, size = struct.unpack_from('<2Q', self.data, 0xa8)
        assert table == 0xe8 and size % 72 == 0
        self.nodes = {}
        for p in range(table, table + size, 72):
            ident = struct.unpack_from('<Q', self.data, p)[0]
            name = self.data[p+8:p+40].split(b'\0')[0].decode()
            off, length = struct.unpack_from('<2Q', self.data, p+56)
            assert off + length <= self.length
            self.nodes[ident] = (name, self.base + off, length)

    def values(self, ident, name=None):
        actual, off, size = self.nodes[ident]
        assert name is None or actual == name, (actual, name)
        assert size % 8 == 0
        return list(struct.unpack_from('<' + 'Q' * (size // 8), self.data, off))

    def registers(self, ident, count):
        rows = self.values(ident, 'regSetting')
        assert len(rows) == count * 10
        result = []
        for p in range(0, len(rows), 10):
            slave, _, addr, n, data, atype, dtype, op, delayed, delay = rows[p:p+10]
            assert slave == 0 and atype == 2 and dtype in (1, 2) and op == 0 and n == 1
            value, = self.values(data, 'registerData')
            wait = self.values(delay, 'delayUs')[0] if delayed else 0
            result.append(dict(address=addr, value=value, bits=dtype*8, delay_us=wait))
        return result

    def decode(self):
        ident = next(i for i, v in self.nodes.items() if v[0] == 'sensorDriverData')
        root = self.values(ident)
        assert len(root) == 95
        _, off, size = self.nodes[root[4]]
        name = self.data[off:off+size].split(b'\0')[0].decode()
        info = self.values(root[41], 'resolutionInfo')
        assert len(info) == 4 and info[0] == 1
        modes = self.values(info[3], 'resolutionData')
        assert len(modes) == info[2] * 36
        decoded_modes = []
        for p in range(0, len(modes), 36):
            mode = modes[p:p+36]
            stream = self.values(mode[2], 'streamConfiguration')
            registers = self.registers(mode[19], mode[18])
            decoded_modes.append(dict(width=stream[4], height=stream[5], bits=stream[6],
                line_length=mode[3], frame_length=mode[4], pixel_rate=mode[7],
                lanes=mode[11], fps=struct.unpack('<d', struct.pack('<Q', mode[10]))[0],
                registers=registers))
        init = self.values(root[70], 'initSettings')
        assert len(init) == root[69] * 3
        inits = []
        for p in range(0, len(init), 3):
            inits += self.registers(init[p+2], init[p+1])
        return dict(name=name, sha256=hashlib.sha256(self.data).hexdigest(),
            address_8bit=root[5], address_7bit=root[5] >> 1,
            chip_id_register=root[8], chip_id=root[9],
            power_up=self.values(root[13], 'powerSetting'),
            power_down=self.values(root[15], 'powerSetting'),
            lane_assign=[self.values(i)[0] for i,v in self.nodes.items() if v[0]=='laneAssign'],
            modes=decoded_modes, init=inits,
            start=self.registers(root[58], root[57]), stop=self.registers(root[60], root[59]))


def emit_segments(name, registers):
    # Split at stock delays so cci_multi_reg_write never removes a wait.
    groups, group = [], []
    for reg in registers:
        group.append(reg)
        if reg['delay_us']:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    lines = []
    for i, group in enumerate(groups):
        lines.append(f'static const struct cci_reg_sequence {name}_{i}[] = {{')
        for r in group:
            lines.append(f'\t{{ CCI_REG{r["bits"]}(0x{r["address"]:04x}), 0x{r["value"]:04x} }},')
        lines.append('};')
    lines.append(f'static const struct sns_seg {name}[] = {{')
    for i, group in enumerate(groups):
        lines.append(f'\t{{ {name}_{i}, ARRAY_SIZE({name}_{i}), {group[-1]["delay_us"]} }},')
    lines.append('};\n')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('module', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    result = Module(args.module).decode()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'sensor.json').write_text(json.dumps(result, indent=2)+'\n')
    if result['chip_id'] in (0x30c6, 0x0556):
        # Stock res1: same full field of view, 2x2 binning, 2104x1560.
        mode = result['modes'][1 if result['chip_id'] == 0x30c6 else 0]
        regs = {r['address']: r['value'] for r in mode['registers']}
        # 0x0820 is the stock requested per-lane Mbps, DDR link clock is half.
        freq = regs[0x0820] * 1000000 // 2 if result['chip_id'] == 0x30c6 else 416000000
        header = '/* Generated from stock module SHA256 '+result['sha256']+'. Keep local. */\n'
        for name, registers in [('sns_init', result['init']), ('sns_start', result['start']),
                                ('sns_stop', result['stop']), ('sns_preview', mode['registers'])]:
            header += emit_segments(name, registers)
        header += f'static const s64 sns_link_freq_menu[] = {{ {freq}LL }};\n'
        header += 'static const struct sns_mode sns_modes[] = {\n\t{\n'
        header += f'\t\t.width = {mode["width"]}, .height = {mode["height"]},\n'
        header += f'\t\t.hts = {mode["line_length"]}, .vts = {mode["frame_length"]},\n'
        header += f'\t\t.exposure = {min(1000, mode['frame_length']-8)}, .exposure_margin = 8,\n'
        header += f'\t\t.link_freq_index = 0, .pixel_rate = {mode["pixel_rate"]}ULL,\n'
        header += '\t\t.segs = sns_preview, .num_segs = ARRAY_SIZE(sns_preview),\n\t},\n};\n'
        (args.out/('s5k3l6-stock.h' if result['chip_id'] == 0x30c6 else 'hi556-stock.h')).write_text(header)
    print(f'{result["name"]}: address 0x{result["address_7bit"]:02x}, id 0x{result["chip_id"]:04x}; {len(result["modes"])} modes')


if __name__ == '__main__':
    main()
