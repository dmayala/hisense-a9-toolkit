#!/usr/bin/env python3
"""Disassemble a vaddr range of an AArch64 ELF (PIE, vaddr==file offset for .text)
with symbols from a separate symbol ELF (e.g. .gnu_debugdata minidebuginfo).
usage: python3 -I elfdis.py BIN SYMELF START END   (hex)    or  ... BIN SYMELF name
"""
import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

def syms(path):
    out = {}
    with open(path, 'rb') as f:
        e = ELFFile(f)
        for s in e.iter_sections():
            if s.header.sh_type in ('SHT_SYMTAB', 'SHT_DYNSYM'):
                for y in s.iter_symbols():
                    if y['st_value'] and y.name:
                        out[y['st_value']] = (y.name, y['st_size'])
    return out

def main():
    data = open(sys.argv[1], 'rb').read()
    S = syms(sys.argv[2])
    if len(sys.argv) == 4:
        n = sys.argv[3]
        a = [k for k, v in S.items() if v[0] == n or n in v[0]]
        for k in a: print(hex(k), S[k])
        return
    lo, hi = int(sys.argv[3], 16), int(sys.argv[4], 16)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for i in md.disasm(data[lo:hi], lo):
        if i.address in S: print(f'<{S[i.address][0]}>:')
        t = ''
        if i.mnemonic == 'bl' or i.mnemonic == 'b':
            try:
                tgt = int(i.op_str.lstrip('#'), 16)
                if tgt in S: t = '  ; ' + S[tgt][0]
            except ValueError: pass
        print(f'{i.address:08x}: {i.mnemonic:8s}{i.op_str}{t}')

main()
