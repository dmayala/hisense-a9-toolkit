#!/usr/bin/env python3
"""Disassemble functions of a symbolized arm64 kernel ELF (vmlinux-to-elf output).

Usage: python3 -I kern-dis.py <vmlinux.elf> <symbol|0xaddr[:size]> [...]
       python3 -I kern-dis.py <vmlinux.elf> --hex 0xaddr:size     (raw bytes dump)

Annotates bl/b targets with symbol names and adrp+add/ldr pairs with the target
symbol or the C string found there. Needs capstone and pyelftools
(nix-shell -p python3Packages.capstone python3Packages.pyelftools).
Read-only; prints to stdout. Run with python3 -I (the input is untrusted data).
"""
import bisect
import sys

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs
from elftools.elf.elffile import ELFFile


class Kern:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.elf = ELFFile(self.f)
        self.segs = [(s["p_vaddr"], s["p_offset"], s["p_filesz"])
                     for s in self.elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        syms = {}
        for sec in self.elf.iter_sections():
            if sec.header["sh_type"] != "SHT_SYMTAB":
                continue
            for s in sec.iter_symbols():
                if s.name and s["st_value"]:
                    syms.setdefault(s["st_value"], s.name)
        self.addrs = sorted(syms)
        self.names = [syms[a] for a in self.addrs]
        self.byname = {}
        for a, n in zip(self.addrs, self.names):
            self.byname.setdefault(n, []).append(a)

    def read(self, va, n):
        for v, o, sz in self.segs:
            if v <= va < v + sz:
                self.f.seek(o + va - v)
                return self.f.read(min(n, v + sz - va))
        return b""

    def sym(self, va):
        i = bisect.bisect_right(self.addrs, va) - 1
        if i < 0:
            return hex(va)
        off = va - self.addrs[i]
        return self.names[i] + (f"+{off:#x}" if off else "")

    def size(self, va):
        i = bisect.bisect_right(self.addrs, va)
        return (self.addrs[i] - va) if i < len(self.addrs) else 0x100

    def cstr(self, va):
        b = self.read(va, 120)
        z = b.find(b"\0")
        s = b[:z if z >= 0 else 120]
        if len(s) > 2 and s[0] == 1:  # KERN_<level> prefix
            s = s[2:]
        if len(s) >= 3 and all(32 <= c < 127 or c in (9, 10) for c in s):
            return repr(s.decode())
        return None


def dis(k, va, size):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    code = k.read(va, size)
    pages = {}
    for ins in md.disasm(code, va):
        note = ""
        ops = ins.op_str
        if ins.mnemonic in ("bl", "b") or ins.mnemonic.startswith("b."):
            try:
                t = int(ops.split("#")[-1], 16)
                note = k.sym(t)
            except ValueError:
                pass
        if ins.mnemonic == "adrp":
            reg = ops.split(",")[0]
            pages[reg] = int(ops.split("#")[-1], 16)
        elif ins.mnemonic in ("add", "ldr", "ldrb", "ldrh", "ldrsw", "str", "strb") and "#" in ops:
            parts = [p.strip() for p in ops.replace("[", "").replace("]", "").split(",")]
            base = parts[1] if ins.mnemonic == "add" else parts[1]
            if base in pages:
                try:
                    imm = int(parts[-1].lstrip("#"), 16) if parts[-1].startswith("#0x") else int(parts[-1].lstrip("#"))
                    t = pages[base] + imm
                    s = k.cstr(t)
                    note = f"{t:#x} {k.sym(t)}" + (f" {s}" if s else "")
                except ValueError:
                    pass
        print(f"{ins.address:#x}: {ins.mnemonic:8s} {ops:40s} {('; ' + note) if note else ''}")


def xref(k, target):
    """Find adrp+add/ldr pairs (within 8 insns, any register) and bl that reach target."""
    import struct
    for v, o, sz in k.segs:
        if v < 0xffffff8008080000 or v > 0xffffff8009c00000:
            continue
        k.f.seek(o)
        code = k.f.read(sz)
        n = len(code) // 4
        words = struct.unpack("<%dI" % n, code[:n * 4])
        for i, w in enumerate(words):
            pc = v + 4 * i
            if (w & 0xFC000000) == 0x94000000:  # bl
                imm = w & 0x3FFFFFF
                if imm & 0x2000000:
                    imm -= 0x4000000
                if pc + 4 * imm == target:
                    print(f"{pc:#x} bl   in {k.sym(pc)}")
            if (w & 0x9F000000) == 0x90000000:  # adrp
                rd = w & 31
                imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
                if imm & (1 << 20):
                    imm -= 1 << 21
                page = (pc & ~0xFFF) + (imm << 12)
                if page != target & ~0xFFF:
                    continue
                for j in range(1, 9):
                    if i + j >= n:
                        break
                    w2 = words[i + j]
                    if (w2 & 0xFF800000) == 0x91000000 and ((w2 >> 5) & 31) == rd:  # add imm
                        if page + ((w2 >> 10) & 0xFFF) == target:
                            print(f"{pc:#x} adrp+add in {k.sym(pc)}")
                    if (w2 & 0x3B000000) == 0x39000000 and ((w2 >> 5) & 31) == rd:  # ldr/str uimm
                        sc = w2 >> 30
                        if page + (((w2 >> 10) & 0xFFF) << sc) == target:
                            print(f"{pc:#x} adrp+ld/st in {k.sym(pc)}")


def main():
    k = Kern(sys.argv[1])
    args = sys.argv[2:]
    if args and args[0] == "--str":
        needle = args[1].encode() + b"\0"
        for v, o, sz in k.segs:
            k.f.seek(o)
            blob = k.f.read(sz)
            i = blob.find(needle)
            while i >= 0:
                print(f"{v + i:#x} {args[1]}")
                xref(k, v + i)
                i = blob.find(needle, i + 1)
        return
    if args and args[0] == "--xref":
        xref(k, int(args[1], 16))
        return
    if args and args[0] == "--hex":
        a, n = args[1].split(":")
        a, n = int(a, 16), int(n, 0)
        b = k.read(a, n)
        for i in range(0, len(b), 16):
            print(f"{a + i:#x}: {b[i:i + 16].hex(' ')}")
        return
    for t in args:
        if t.startswith("0x"):
            a, _, n = t.partition(":")
            a = int(a, 16)
            n = int(n, 0) if n else k.size(a)
            vas = [(a, n)]
        else:
            vas = [(a, k.size(a)) for a in k.byname.get(t, [])]
        for a, n in vas:
            print(f"==== {k.sym(a)} @ {a:#x} size {n:#x}")
            dis(k, a, n)


if __name__ == "__main__":
    main()
