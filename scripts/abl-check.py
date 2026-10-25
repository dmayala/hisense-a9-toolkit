#!/usr/bin/env python3
"""abl-check.py - run the Hisense A9's OWN bootloader (ABL / LinuxLoader) image
checks on a boot or recovery image, offline, in Unicorn. No phone access.

The ABL code is executed instruction for instruction from the phone's abl_a
partition (golden dump); only UEFI firmware services, debug printing and the
PMIC query are supplied by the host. Any other call that leaves the reviewed
set of ABL functions stops the run with an error ("fail loudly").

Stages (RVAs are for LinuxLoader sha256 1dd55dcb..., the build on the phone):
  1 header   CheckImageHeader            0x132a0
  2 dtbo     LoadAndValidateDtboImg      0x23d78  (recovery: recovery_dtbo
             from the image; boot: the dtbo partition)
  3 dtb      GetSocDtb 0x156b8, GetBoardDtb 0x16678, AppendToDtList 0x17228,
             ufdt arena 0x2cc30, ufdt_install_blob 0x28260,
             ufdt_apply_multioverlay 0x28bd8   (overlay application)
See docs/abl-check.md for how these were found and what is NOT emulated.

Usage:
  abl-check.py <image> {recovery|boot | --target recovery|boot} [--dtbo dtbo.img] [--abl abl.bin]
               [--json] [--quiet] [--save-dtb merged.dtb] [--partition-size N]
Exit: 0 all stages PASS, 1 a stage FAILED, 2 usage / harness error.
Run through scripts/abl-check.sh, which provides unicorn via nix-shell.
"""
import argparse
import bisect
import hashlib
import json
import lzma
import os
import struct
import sys

from unicorn import (Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED,
                     UC_HOOK_INTR, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                 UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_LR, UC_ARM64_REG_PC,
                                 UC_ARM64_REG_CPACR_EL1)


RW = UC_PROT_READ | UC_PROT_WRITE

A9_DATA = os.environ.get('A9_DATA', os.path.expanduser('~/a9-assets'))
GOLDEN = os.path.join(A9_DATA, 'Backups/edl_2026-10-17_102417')
DEFAULT_ABL = os.path.join(GOLDEN, 'lun4/abl_a.bin')
DEFAULT_DTBO = os.path.join(GOLDEN, 'lun4/dtbo_a.bin')
# LinuxLoader PE carved from the phone's abl_a (FV at 0x3000, LZMA, MZ at 0xb8).
PE_SHA256 = '1dd55dcbb57612f949a93e94c926b3a69924f5941037e0a350ce022e25bc4dab'

# Partition sizes (scripts/partition-sizes.txt). ImageSize = whole partition is
# INFERRED: vbmeta flags=3 (verification disabled) makes libavb load the full
# partition. The host-side file check below covers the stricter case.
PART_SIZE = {'recovery': 96 << 20, 'boot': 96 << 20}
DTBO_PART_SIZE = 24 << 20

# ---------------------------------------------------------------- RVAs
RVA = {
    'CheckImageHeader': 0x132a0,
    'LoadAndValidateDtboImg': 0x23d78,
    'GetSocDtb': 0x156b8,
    'GetBoardDtb': 0x16678,
    'AppendToDtList': 0x17228,
    'DeleteDtList': 0x172d8,
    'DtoArenaInit': 0x2cc30,
    'ufdt_install_blob': 0x28260,
    'ufdt_apply_multioverlay': 0x28bd8,
    'DeviceTreeAppended': 0x140d8,
    'fdt_check_header': 0x40560,
    # host fixtures
    'DebugPrint': 0x1b50,
    'DebugAssert': 0x1e98,
    'StackChkFail': 0xfa48,
    'dto_print': 0x290a0,
    'BoardPmicModel': 0x10a00,
    'BoardPmicTarget': 0x10c18,
    'GetTimerCountms': 0x1a3e8,
    'RticMpData': 0x25488,
}
G_BS = 0x5fb98             # gBS pointer (EFI_BOOT_SERVICES*)
G_SAFESTACK_INIT = 0x6ecb0  # byte: unsafe stack initialised
G_SAFESTACK_PTR = 0x6ecb8   # __safestack_unsafe_stack_ptr
G_BOARDINFO = 0x5fbd4       # struct BoardInfo
G_DTBO_NOT_NEEDED = 0x5fc28  # set by GetSocDtb on an exact match
TEXT = (0x1000, 0x5a000)

# Board fixture, measured from the stock boot log (kmonlog 2026-08-11, kernel
# socinfo_print + XBL CDT line): SoC id 444 v1.0, QRD (11) v1.0 subtype 0,
# foundry 2. PMICs PM6125 v1.1 (model 45) and PMI632 v2.0 (model 37).
BOARD = {'platform': 11, 'version': 0x10000, 'subtype': 0, 'chip_id': 444,
         'chip_version': 0x10000, 'foundry': 2}
PMIC_MODEL = [45, 37, 0, 0]
# BoardPmicTarget = AllLayerRev<<16 | MetalRev<<8 | Model (CAF Board.c); the
# revisions come from the kernel's "PM6125 v1.1" / "PMI632 v2.0" lines.
PMIC_TARGET = [0x1012d, 0x20025, 0, 0]

# ---------------------------------------------------------------- memory map
BASE = 0x40000000          # PE load address (relocated; page 0 stays unmapped)
STACK = 0x10000000; STACK_SZ = 0x100000
USTACK = 0x11000000; USTACK_SZ = 0x100000
TRAP = 0x12000000          # gBS table + trap stubs; RET sentinel inside
GBS_TABLE = TRAP
GBS_STUBS = TRAP + 0x1000
RET = TRAP + 0x3000
HEAP = 0x14000000; HEAP_SZ = 0x4000000
IMG = 0x20000000           # boot/recovery partition buffer
DTBO = 0x30000000          # dtbo partition buffer

EFI_ERR = 1 << 63
STATUS = {0: 'EFI_SUCCESS', 2: 'EFI_INVALID_PARAMETER', 3: 'EFI_UNSUPPORTED',
          4: 'EFI_BAD_BUFFER_SIZE', 5: 'EFI_BUFFER_TOO_SMALL', 9: 'EFI_OUT_OF_RESOURCES',
          12: 'EFI_NO_MEDIA', 14: 'EFI_NOT_FOUND', 21: 'EFI_ABORTED'}

# ABL functions the emulated stages may enter. Every entry was reviewed
# (scratch disassembly, docs/abl-check.md): pure library code (BaseLib,
# BaseMemoryLib, libfdt, libufdt), SafeStack/stack-protector glue, or the
# DT-matching code itself. A call to any other ABL function entry raises.
ALLOWED = set()  # filled below, from ALLOWED_TEXT

class HarnessError(Exception):
    pass


def status_name(v):
    if v & EFI_ERR:
        return STATUS.get(v & ~EFI_ERR, 'EFI_ERROR(%#x)' % (v & ~EFI_ERR))
    return 'EFI_SUCCESS' if v == 0 else 'WARN(%#x)' % v


# ---------------------------------------------------------------- ABL loading
def extract_linuxloader(abl_path):
    data = open(abl_path, 'rb').read()
    fv = data.find(b'_FVH')
    if fv < 0x28:
        raise HarnessError('no UEFI firmware volume in %s' % abl_path)
    pos = fv - 0x28
    while True:
        pos = data.find(b'\x5d\x00\x00', pos + 1)
        if pos < 0 or pos > fv + 0x10000:
            raise HarnessError('no LZMA section in the firmware volume')
        try:
            dec = lzma.decompress(data[pos:], format=lzma.FORMAT_ALONE)
        except lzma.LZMAError:
            continue
        mz = dec.find(b'MZ')
        if mz >= 0:
            pe = dec[mz:]
            break
    digest = hashlib.sha256(pe).hexdigest()
    if digest != PE_SHA256:
        raise HarnessError('LinuxLoader sha256 %s is not the pinned build %s; the RVAs in '
                           'this script are only valid for that build' % (digest, PE_SHA256))
    return pe


def map_pe(pe, base):
    off = struct.unpack_from('<I', pe, 0x3c)[0]
    assert pe[off:off + 4] == b'PE\0\0' and struct.unpack_from('<H', pe, off + 4)[0] == 0xaa64
    count = struct.unpack_from('<H', pe, off + 6)[0]
    opt = off + 24
    opt_len = struct.unpack_from('<H', pe, off + 20)[0]
    image_base = struct.unpack_from('<Q', pe, opt + 24)[0]
    size, headers = struct.unpack_from('<II', pe, opt + 56)
    img = bytearray(size)
    img[:headers] = pe[:headers]
    for i in range(count):
        s = opt + opt_len + i * 40
        vsz, rva, rsz, roff = struct.unpack_from('<IIII', pe, s + 8)
        img[rva:rva + rsz] = pe[roff:roff + rsz]
    # base relocations (DIR64 only, as emitted for this image)
    reloc_rva, reloc_sz = struct.unpack_from('<II', pe, opt + 112 + 5 * 8)
    delta = base - image_base
    p, end, fixed = reloc_rva, reloc_rva + reloc_sz, 0
    while p < end:
        page, blk = struct.unpack_from('<II', img, p)
        if blk < 8:
            break
        for k in range((blk - 8) // 2):
            e = struct.unpack_from('<H', img, p + 8 + 2 * k)[0]
            typ, o = e >> 12, e & 0xfff
            if typ == 10:
                v = struct.unpack_from('<Q', img, page + o)[0]
                struct.pack_into('<Q', img, page + o, (v + delta) & (2 ** 64 - 1))
                fixed += 1
            elif typ != 0:
                raise HarnessError('unsupported relocation type %d' % typ)
        p += blk
    return bytes(img), fixed


def bl_targets(img):
    """Every direct BL target in .text = the set of function entries."""
    out = set()
    lo, hi = TEXT
    for a in range(lo, hi, 4):
        w = struct.unpack_from('<I', img, a)[0]
        if (w & 0xfc000000) == 0x94000000:
            imm = w & 0x3ffffff
            if imm & 0x2000000:
                imm -= 0x4000000
            t = a + imm * 4
            if lo <= t < hi:
                out.add(t)
    return out


# ---------------------------------------------------------------- printf
def efi_format(read_str, read_wstr, fmt, args):
    out, i, ai = [], 0, 0

    def nxt():
        nonlocal ai
        v = args(ai)
        ai += 1
        return v
    while i < len(fmt):
        c = fmt[i]
        if c != '%':
            out.append(c)
            i += 1
            continue
        i += 1
        flags = ''
        while i < len(fmt) and fmt[i] in '-+ 0,#':
            flags += fmt[i]
            i += 1
        width = ''
        while i < len(fmt) and (fmt[i].isdigit() or fmt[i] == '*'):
            width += fmt[i]
            i += 1
        if width == '*':
            width = str(nxt() & 0xffffffff)
        if i < len(fmt) and fmt[i] == '.':
            i += 1
            while i < len(fmt) and (fmt[i].isdigit() or fmt[i] == '*'):
                i += 1
        long_ = False
        while i < len(fmt) and fmt[i] in 'lLz':
            long_ = True
            i += 1
        if i >= len(fmt):
            break
        t = fmt[i]
        i += 1
        if t == '%':
            out.append('%')
            continue
        v = nxt()
        if t in 'dixXuc' and not long_:
            v &= 0xffffffff
        if t in 'di':
            if v >= (1 << 63 if long_ else 1 << 31):
                v -= (1 << 64 if long_ else 1 << 32)
            s = str(v)
        elif t == 'u':
            s = str(v)
        elif t in 'xX':
            s = ('%x' if t == 'x' else '%X') % v
        elif t == 'p':
            s = '%016x' % v
        elif t == 'c':
            s = chr(v & 0xff)
        elif t == 'a':
            s = read_str(v)
        elif t in 'sS':
            s = read_wstr(v)
        elif t == 'r':
            s = status_name(v)
        elif t == 'g':
            s = '<guid@%#x>' % v
        else:
            s = '<%%%s?>' % t
        if width and width.isdigit():
            s = s.rjust(int(width), '0' if '0' in flags and '-' not in flags else ' ')
        out.append(s)
    return ''.join(out)


# ---------------------------------------------------------------- emulator
class Abl:
    REGS = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
            UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7]

    def __init__(self, img, entries, discover=False):
        self.entries = entries
        self.discover = discover
        self.discovered = {}
        self.log = []
        self.calls = []
        self.error = None
        self.heap = HEAP
        self.pmic_queries = []
        self.merged_dtb = None
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        # Only .text is executable; headers, .data, .reloc and every data
        # region are mapped without EXEC, so a stray jump faults.
        uc.mem_map(BASE, TEXT[0], UC_PROT_READ)
        uc.mem_map(BASE + TEXT[0], TEXT[1] - TEXT[0], UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_map(BASE + TEXT[1], ((len(img) + 0xfff) & ~0xfff) - TEXT[1], RW)
        uc.mem_write(BASE, img)
        for a, s in [(STACK, STACK_SZ), (USTACK, USTACK_SZ), (HEAP, HEAP_SZ)]:
            uc.mem_map(a, s, RW)
        uc.mem_map(TRAP, 0x4000, UC_PROT_ALL)  # gBS table + stubs (hooked)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)  # FP/SIMD on
        # SafeStack: mark initialised, point at our unsafe stack.
        self.w8(BASE + G_SAFESTACK_INIT, 1)
        self.w64(BASE + G_SAFESTACK_PTR, USTACK + USTACK_SZ - 0x100)
        # gBS: every slot points at its own trap stub.
        for o in range(0, 0x200, 8):
            self.w64(GBS_TABLE + o, GBS_STUBS + o)
        self.w64(BASE + G_BS, GBS_TABLE)
        self.fixtures = {
            BASE + RVA['DebugPrint']: self._debug_print,
            BASE + RVA['dto_print']: self._dto_print,
            BASE + RVA['DebugAssert']: self._assert,
            BASE + RVA['StackChkFail']: self._stack_chk,
            BASE + RVA['BoardPmicModel']: self._pmic_model,
            BASE + RVA['BoardPmicTarget']: self._pmic_target,
            BASE + RVA['GetTimerCountms']: lambda: self.ret(0),
            BASE + RVA['RticMpData']: self._rtic,
        }
        self.services = {0x40: self._allocate_pool, 0x48: self._free_pool,
                         0x160: self._copy_mem, 0x168: self._set_mem}
        # Block hooks only on code that is NOT reviewed: allowed functions run
        # without callbacks. A function's range is [entry, next entry). The
        # bodies of host fixtures stay forbidden; only their entry is let
        # through, where the code hook takes over.
        allowed = ALLOWED
        self.fixture_rvas = {a - BASE for a in self.fixtures}
        bounds = sorted(entries | allowed | self.fixture_rvas | {TEXT[0], TEXT[1]})
        self.bounds = bounds
        lo = None
        for a, b in zip(bounds, bounds[1:]):
            if a in allowed:
                if lo is not None:
                    uc.hook_add(UC_HOOK_BLOCK, self._block, begin=BASE + lo, end=BASE + a - 1)
                    lo = None
            elif lo is None:
                lo = a
        if lo is not None:
            uc.hook_add(UC_HOOK_BLOCK, self._block, begin=BASE + lo, end=BASE + TEXT[1] - 1)
        for a in self.fixtures:
            uc.hook_add(UC_HOOK_CODE, self._fixture, begin=a, end=a)
        uc.hook_add(UC_HOOK_CODE, self._trap, begin=TRAP, end=TRAP + 0x3fff)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        uc.hook_add(UC_HOOK_INTR, self._intr)

    # -- memory helpers
    def r(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def r32(self, a):
        return struct.unpack('<I', self.r(a, 4))[0]

    def r64(self, a):
        return struct.unpack('<Q', self.r(a, 8))[0]

    def w8(self, a, v):
        self.uc.mem_write(a, bytes([v & 0xff]))

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack('<I', v & 0xffffffff))

    def w64(self, a, v):
        self.uc.mem_write(a, struct.pack('<Q', v & (2 ** 64 - 1)))

    def cstr(self, a, limit=512):
        if not a:
            return '(null)'
        try:
            b = self.r(a, limit)
        except UcError:
            return '<bad ptr %#x>' % a
        return b.split(b'\0')[0].decode(errors='replace')

    def wstr(self, a, limit=512):
        if not a:
            return '(null)'
        b = self.r(a, limit)
        if len(b) > 1 and b[1] != 0:  # vendor code passing CHAR8 to %s
            return b.split(b'\0')[0].decode(errors='replace')
        out = []
        for k in range(0, len(b) - 1, 2):
            ch = b[k] | b[k + 1] << 8
            if not ch:
                break
            out.append(chr(ch))
        return ''.join(out)

    def reg(self, i):
        return self.uc.reg_read(self.REGS[i])

    def ret(self, v=0):
        self.uc.reg_write(UC_ARM64_REG_X0, v & (2 ** 64 - 1))
        self.uc.reg_write(UC_ARM64_REG_PC, self.uc.reg_read(UC_ARM64_REG_LR))

    def alloc(self, n, align=16):
        p = (self.heap + align - 1) & ~(align - 1)
        if p + n > HEAP + HEAP_SZ:
            raise HarnessError('emulated heap exhausted')
        self.heap = p + n
        return p

    def stop(self, msg):
        if self.error is None:
            self.error = msg
        self.uc.emu_stop()

    # -- hooks
    def _block(self, uc, address, size, _):
        rva = address - BASE
        if rva in self.fixture_rvas:
            return
        k = bisect.bisect_right(self.bounds, rva) - 1
        fn = self.bounds[k]
        if self.discover:
            self.discovered.setdefault(fn, (rva, uc.reg_read(UC_ARM64_REG_LR) - BASE))
            return
        self.stop('unexpected call into ABL code %#x (function %#x, LR %#x): not in the '
                  'reviewed allowlist' % (rva, fn, uc.reg_read(UC_ARM64_REG_LR) - BASE))

    def _fixture(self, uc, address, size, _):
        try:
            self.fixtures[address]()
        except HarnessError as e:
            self.stop(str(e))

    def _trap(self, uc, address, size, _):
        if address == RET:
            uc.emu_stop()
            return
        off = address - GBS_STUBS
        if 0 <= off < 0x200 and off in self.services:
            try:
                self.services[off]()
            except HarnessError as e:
                self.stop(str(e))
            return
        self.stop('unexpected UEFI service call gBS+%#x from LR %#x'
                  % (off, uc.reg_read(UC_ARM64_REG_LR) - BASE))

    def _unmapped(self, uc, access, address, size, value, _):
        self.stop('unmapped memory access at %#x (pc rva %#x)'
                  % (address, uc.reg_read(UC_ARM64_REG_PC) - BASE))
        return False

    def _intr(self, uc, intno, _):
        self.stop('exception/interrupt %d at pc rva %#x' % (intno, uc.reg_read(UC_ARM64_REG_PC) - BASE))

    # -- host fixtures
    def _varargs(self, first):
        sp = self.uc.reg_read(UC_ARM64_REG_SP)

        def get(i):
            k = first + i
            return self.reg(k) if k < 8 else self.r64(sp + 8 * (k - 8))
        return get

    def _debug_print(self):
        level, fmt = self.reg(0), self.cstr(self.reg(1), 1024)
        msg = efi_format(self.cstr, self.wstr, fmt, self._varargs(2))
        self.log.append(('ERROR' if level & 0x80000000 else 'INFO' if level & 0x40 else
                         'VERBOSE' if level & 0x400000 else '%#x' % level, msg.rstrip('\n')))
        self.ret(0)

    def _dto_print(self):
        msg = efi_format(self.cstr, self.wstr, self.cstr(self.reg(0), 1024), self._varargs(1))
        self.log.append(('UFDT', msg.rstrip('\n')))
        self.ret(0xffffffff)  # the ABL's dto_print is a stub returning -1

    def _assert(self):
        raise HarnessError('ABL ASSERT %s:%d %s' % (self.cstr(self.reg(0)).split('/')[-1],
                                                    self.reg(1), self.cstr(self.reg(2))))

    def _stack_chk(self):
        raise HarnessError('ABL stack protector fired (__stack_chk_fail)')

    def _rtic(self):
        # GetSocDtb hands a DTB with no match flags (the trailing RTIC DTB) to
        # this routine (0x25488). It reads qcom,rtic-id / MP_DATA and passes
        # them to TrustZone (LocateProtocol SCM + ScmSipSysCall); every path
        # past the property reads returns 1, success or not. The value only
        # picks a debug message in GetSocDtb (0x1584c); DT selection goes on
        # with the next DTB either way. Not emulated: the TrustZone call.
        self.calls.append(('RticMpData', self.reg(0) - IMG))
        self.ret(1)

    def _pmic_model(self):
        i = self.reg(0) & 0xffffffff
        if i >= len(PMIC_MODEL):
            raise HarnessError('BoardPmicModel(%d) out of range' % i)
        self.pmic_queries.append(('model', i))
        self.ret(PMIC_MODEL[i])

    def _pmic_target(self):
        i = self.reg(0) & 0xffffffff
        if i >= len(PMIC_TARGET):
            raise HarnessError('BoardPmicTarget(%d) out of range' % i)
        self.pmic_queries.append(('target', i))
        self.ret(PMIC_TARGET[i])

    # -- UEFI boot services (the only firmware the stages touch)
    def _allocate_pool(self):
        n, out = self.reg(1), self.reg(2)  # x0 = pool type
        if n > 0x2000000:
            raise HarnessError('AllocatePool(%#x) larger than any stage needs' % n)
        p = self.alloc(n)
        self.uc.mem_write(p, b'\xa5' * min(n, 0x100000))  # not zeroed, like real pool
        self.w64(out, p)
        self.calls.append(('AllocatePool', n))
        self.ret(0)

    def _free_pool(self):
        self.calls.append(('FreePool', self.reg(0)))
        self.ret(0)

    def _copy_mem(self):
        dst, src, n = self.reg(0), self.reg(1), self.reg(2)
        if n > 0x6000000:
            raise HarnessError('CopyMem(%#x) too large' % n)
        if n:
            self.uc.mem_write(dst, self.r(src, n))
        self.ret(dst)

    def _set_mem(self):
        buf, n, v = self.reg(0), self.reg(1), self.reg(2) & 0xff
        if n > 0x6000000:
            raise HarnessError('SetMem(%#x) too large' % n)
        if n:
            self.uc.mem_write(buf, bytes([v]) * n)
        self.ret(buf)

    # -- calling
    def call(self, rva, args, budget_s=60):
        self.error = None
        for i, v in enumerate(args):
            self.uc.reg_write(self.REGS[i], v)
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + STACK_SZ - 0x1000)
        self.uc.reg_write(UC_ARM64_REG_LR, RET)
        try:
            self.uc.emu_start(BASE + rva, RET, timeout=budget_s * 1000000)
        except UcError as e:
            if self.error is None:
                self.error = 'unicorn: %s at pc rva %#x' % (e, self.uc.reg_read(UC_ARM64_REG_PC) - BASE)
        pc = self.uc.reg_read(UC_ARM64_REG_PC)
        if self.error is None and pc != RET:
            self.error = 'routine %#x did not return (pc rva %#x; time budget %d s)' % (rva, pc - BASE, budget_s)
        if self.error:
            raise HarnessError(self.error)
        return self.uc.reg_read(UC_ARM64_REG_X0)

    def map_buffer(self, addr, data, size):
        size = max(size, len(data))
        self.uc.mem_map(addr, (size + 0xfff) & ~0xfff, RW)
        self.uc.mem_write(addr, data)
        return size


ALLOWED_TEXT = """
# --- EDK2 DebugLib / stack protector / SafeStack glue
0x1e70  DebugAssertEnabled (reads PcdDebugPropertyMask)
0x1fc4  DebugPrintEnabled
0x1fec  DebugPrintLevelEnabled
0x2030  stack-cookie compare (outlined)
0x447c  stack-cookie compare (outlined)
0x27ca0 SafeStack: address of __safestack_unsafe_stack_ptr (init pre-set)
# --- BaseLib / BaseMemoryLib (pure)
0x36dc  AsciiStrLen
0x377c  AsciiStrSize
0x3960  AsciiStrnCmp
0x3ac0  AsciiStrStr
0x3f38  BaseLib outlined fragment
0x3f4c  BaseLib outlined fragment
0x3f74  BaseLib outlined fragment
0x3f98  BaseLib outlined fragment
0x3fb8  BaseLib outlined fragment
0x4038  BaseLib outlined fragment
0x4074  BaseLib outlined fragment
0x40d0  BaseLib outlined fragment
0x3f58  BaseLib outlined fragment
0x40a4  BaseLib outlined fragment
0x209c  InternalSafeStringIsOverlap (tail-branch target; [0x2058,0x209c) = CpuDeadLoop stays forbidden)
0x2134  SafeString overlap wrapper (tail-branch target)
0x2610  AsciiStrnLenS
0x27f4  AsciiStrnCpyS
0x2d94  SafeString outlined fragment
0x2da8  SafeString outlined fragment
0x2de4  SafeString outlined fragment
0x2e00  SafeString outlined fragment
0x2ecc  SafeString outlined fragment
0x2ee8  SafeString outlined fragment
0x2f04  SafeString outlined fragment
0x2f64  SafeString outlined fragment
0x2f70  SafeString outlined fragment
0x2fb8  SafeString outlined fragment
0x4288  DivU64x32 / SwapBytes64 range (pure)
0x4490  SwapBytes32 (fdt32_to_cpu)
0x44e8  SwapBytes16
0x3b1ac memcpy-style copy (pure, used for DT match structs)
0x3b260 memset-style fill (pure)
0x3c984 ZeroMem
0x3ca4c ZeroMem outlined fragment
0x3ca60 ZeroMem / InternalMemZeroMem
0x3cb0c CompareMem
0x3cc38 CompareMem outlined fragment
0x3cc4c SetMem
0x3ccec SetMem outlined fragment
0x3cd00 CopyMem
0x3cddc InternalMemCopyMem
0x3cedc CopyMem outlined fragment
0x3cef8 CopyMem outlined fragment
0x3cfbc stack-cookie fragment + InternalMemSetMem range (pure)
# --- MemoryAllocationLib (reaches the firmware only through gBS->AllocatePool/FreePool)
0x3d3c4 InternalAllocatePool (gBS+0x40)
0x3d414 AllocateZeroPool
0x3d488 AllocatePool
0x3d4ac FreePool (gBS+0x48)
0x3d53c MemoryAllocationLib outlined fragment
0x3d55c MemoryAllocationLib outlined fragment
0x3d5b0 MemoryAllocationLib outlined fragment
0x3d5b8 MemoryAllocationLib tail fragments (any gBS slot it reaches is trapped)
# --- QcomModulePkg BootLib: Board.c getters (read the BoardInfo global, fixture)
0x115f0 BoardPlatformRawChipId
0x11600 BoardPlatformChipVersion
0x11610 BoardPlatformFoundryId
0x11630 BoardPlatformType
0x11640 BoardPlatformSubType
0x11650 BoardTargetId
0x117b8 BoardInfo +0x2c getter
0x13290 IsDynamicPartitionSupport (returns 1)
# --- the routines under test
0x132a0 CheckImageHeader
0x23d78 LoadAndValidateDtboImg
0x131a8 GetImage (loaded-image table lookup)
0x156b8 GetSocDtb
0x15950 ReadDtbFindMatch (qcom,msm-id / board-id / pmic-id matching)
0x16678 GetBoardDtb
0x17228 AppendToDtList
0x172d8 DeleteDtList
0x17310 DTB header bounds check
0x140d8 DeviceTreeAppended (no-DTBO fallback, legacy appended-DTB match)
0x16908 legacy DT list pruning by platform (DeviceTreeAppended)
0x16cb0 legacy DT list pruning by soc/pmic (DeviceTreeAppended)
# --- libfdt (pure)
0x3d810 fdt64 read -> SwapBytes64
0x3e8b8 libfdt
0x3e910 libfdt
0x3e9b0 libfdt
0x3ea20 libfdt
0x3eb90 fdt_path_offset
0x3ee20 fdt_get_property_namelen
0x3f0f8 fdt_getprop
0x3f9e8 libfdt
0x3fbf8 libfdt
0x3fce0 libfdt
0x3fe98 libfdt
0x3ff30 libfdt
0x3fff8 libfdt
0x400e0 libfdt
0x40120 libfdt
0x401e0 libfdt
0x402b0 libfdt
0x40340 libfdt
0x40560 fdt_check_header
0x406d0 libfdt
0x40760 fdt_next_tag
0x40928 libfdt
0x409a0 libfdt
0x40e20 libfdt
# --- libufdt (pure; dto_malloc goes through AllocatePool)
0x28260 ufdt_install_blob
0x28348 ufdt_overlay_apply
0x28bd8 ufdt_apply_multioverlay
0x28e68 libufdt (recursive)
0x29100 qsort (indirect calls go to ufdt comparators in .text)
0x2cbf8 libufdt
0x2cc30 dto arena init (AllocateZeroPool 0x500000)
0x2cce0 libufdt
0x2cd50 libufdt
0x2cd88 libufdt
0x2ce38 libufdt
0x2ce80 libufdt
0x2d060 libufdt
0x2d098 libufdt
0x2d0d0 libufdt
0x2d110 libufdt
0x2d158 libufdt
0x2d1a0 libufdt (/aliases)
0x2d308 libufdt
0x2d368 libufdt
0x2d420 libufdt
0x2d560 libufdt
0x2d630 libufdt
0x2d6c8 libufdt
0x2d770 libufdt
0x2d858 libufdt
0x2d950 libufdt
0x2da58 libufdt
0x2db60 libufdt
0x2dc28 libufdt
0x2dd20 libufdt
0x2dda8 libufdt
0x2de68 libufdt
0x2df98 libufdt
0x2dff8 libufdt
0x2e128 libufdt
0x2e188 libufdt
0x2e208 libufdt
0x2e2a8 libufdt (phandle)
0x2e3d0 libufdt
0x2e488 libufdt
0x2e830 libufdt
0x2e9d8 libufdt
0x2ea38 libufdt
0x2ea88 libufdt
0x2ec50 libufdt
0x2ed88 libufdt
"""


def _load_allowlist():
    for line in ALLOWED_TEXT.splitlines():
        line = line.split('#')[0].strip()
        if line:
            ALLOWED.add(int(line.split()[0], 16))

_load_allowlist()


# ---------------------------------------------------------------- FDT (host side, for reporting)
def fdt_root_props(b):
    if len(b) < 40 or struct.unpack_from('>I', b, 0)[0] != 0xd00dfeed:
        return None
    _, tot, off_s, off_str = struct.unpack_from('>4I', b, 0)
    p, path, out = off_s, [], {}
    while p + 4 <= tot:
        tok = struct.unpack_from('>I', b, p)[0]
        p += 4
        if tok == 1:
            e = b.index(b'\0', p)
            path.append(b[p:e])
            p = (e + 4) & ~3
        elif tok == 2:
            path.pop()
        elif tok == 3:
            ln, no = struct.unpack_from('>II', b, p)
            p += 8
            v = b[p:p + ln]
            p = (p + ln + 3) & ~3
            n = b[off_str + no:b.index(b'\0', off_str + no)].decode()
            if len(path) == 1:
                if n in ('model', 'compatible'):
                    out[n] = v.rstrip(b'\0').replace(b'\0', b' ').decode(errors='replace')
                elif n.startswith('qcom,') and n.endswith('-id') and ln % 4 == 0:
                    out[n] = ['%#x' % x for x in struct.unpack('>%dI' % (ln // 4), v)]
        elif tok == 9:
            break
    return out


# ---------------------------------------------------------------- stages
def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def host_checks(image):
    """Not ABL code: the sections the ABL reads must lie inside the file, or
    the partition tail (stale bytes from an earlier flash) is what it reads."""
    if len(image) < 1660:
        return {'pass': True, 'note': 'header too short for section checks'}
    ps = u32(image, 36) or 1
    pg = lambda n: (n + ps - 1) // ps
    rd_size, rd_off = struct.unpack_from('<IQ', image, 1632)
    out = {'file_bytes': len(image)}
    bad = []
    if u32(image, 40) >= 1 and rd_size and rd_off + rd_size > len(image):
        bad.append('recovery_dtbo ends at %d, past the end of the file' % (rd_off + rd_size))
    if u32(image, 40) == 2:
        off = ps * (1 + pg(u32(image, 8)) + pg(u32(image, 16)) + pg(u32(image, 24)) + pg(rd_size))
        if off + u32(image, 1648) > len(image):
            bad.append('DTB section ends at %d, past the end of the file' % (off + u32(image, 1648)))
    out['pass'] = not bad
    if bad:
        out['note'] = '; '.join(bad)
    return out


def stage_header(abl, image, target, hdr_addr):
    """Stage 1: CheckImageHeader(ImageHdrBuffer, 0x1000, VendorHdr, 0,
    &ImageSizeActual, &PageSize, BootIntoRecovery), as LoadImageAndAuthVB2
    calls it at RVA 0x60e8."""
    vendor = abl.alloc(0x1000)
    abl.uc.mem_write(vendor, b'\0' * 0x1000)
    out = abl.alloc(16)
    abl.uc.mem_write(out, b'\0' * 16)
    st = abl.call(RVA['CheckImageHeader'],
                  [hdr_addr, 0x1000, vendor, 0, out, out + 4, 1 if target == 'recovery' else 0])
    return {'rc': st, 'status': status_name(st), 'pass': st == 0,
            'image_size_actual': abl.r32(out), 'page_size': abl.r32(out + 4)}


def make_info(abl, target, header_version, dtbo_addr, dtbo_size):
    """BootInfo fields the stages read (offsets from the disassembly):
    +0 MultiSlotBoot, +1 BootIntoRecovery, +0x128 loaded-image table
    {name*, buffer, size} x N with N at +0x428 (GetImage 0x131a8),
    +0x458 HeaderVersion."""
    info = abl.alloc(0x1000)
    abl.uc.mem_write(info, b'\0' * 0x1000)
    abl.w8(info + 0, 1)                       # A/B device
    abl.w8(info + 1, 1 if target == 'recovery' else 0)
    abl.w32(info + 0x458, header_version)
    names = []
    if dtbo_addr:
        names.append((b'dtbo\0', dtbo_addr, dtbo_size))
    for k, (n, a, s) in enumerate(names):
        p = abl.alloc(len(n))
        abl.uc.mem_write(p, n)
        abl.w64(info + 0x128 + 0x18 * k, p)
        abl.w64(info + 0x130 + 0x18 * k, a)
        abl.w64(info + 0x138 + 0x18 * k, s)
    abl.w64(info + 0x428, len(names))
    return info


def stage_dtbo(abl, info, bp):
    """Stage 2: LoadAndValidateDtboImg(Info, BootParamlist) -> BOOLEAN."""
    ok = abl.call(RVA['LoadAndValidateDtboImg'], [info, bp]) & 0xff
    buf = abl.r64(bp + 0x18)
    res = {'rc': ok, 'pass': ok == 1, 'dtbo_buffer': buf}
    if ok:
        res['source'] = ('image+%#x' % (buf - IMG)) if IMG <= buf < DTBO else 'dtbo partition'
        res['table_entries'] = struct.unpack('>I', abl.r(buf + 16, 4))[0]
    return res


def stage_dtb(abl, image, info, bp, dtbo_ok):
    """Stage 3: the DT selection and overlay sequence of the BootLinux code at
    RVA 0x12514-0x127fc (v2 branch), with the glue arithmetic done here:
    DtbOffset = PageSize * (1 + pages(kernel) + pages(ramdisk) + pages(second)
    + pages(recovery_dtbo)), end = DtbOffset + dtb_size."""
    ps = u32(image, 36)
    pg = lambda n: (n + ps - 1) // ps
    rd_size = u32(image, 1632)
    off = ps * (1 + pg(u32(image, 8)) + pg(u32(image, 16)) + pg(u32(image, 24)) + pg(rd_size))
    end = off + u32(image, 1648)
    res = {'dtb_offset': off}
    if not dtbo_ok:
        # BootLinux 0x12834: with no valid DTBO the ABL calls
        # DeviceTreeAppended(ImageBuffer, end, DtbOffset, DtLoadAddr) and, if
        # that finds a DTB, boots the kernel with it, without any overlay.
        tags = abl.alloc(0x400000)
        dt = abl.call(RVA['DeviceTreeAppended'], [IMG, end, off, tags])
        fb = {'selected': bool(dt)}
        if dt:
            n = struct.unpack('>I', abl.r(dt + 4, 4))[0]
            fb.update(bytes=n, props=fdt_root_props(abl.r(dt, n)))
        res['fallback'] = fb
        res['failed_at'] = 'no DTBO'
        if dt:
            res.update(pass_=False, note='no valid DTBO: the ABL takes its appended-DTB fallback '
                       '(DeviceTreeAppended 0x140d8) and would boot the kernel with that DTB '
                       'alone, model "%s", without the board overlay' % (fb['props'] or {}).get('model'))
            return res
        # 0x128f0: last resort, fdt_check_header(ImageBuffer + PageSize + DtbOffset)
        # (the v0/v1 kernel-relative layout; for a v2 image this is one page
        # past the start of the DTB section). 0x128f4 first rejects DtbOffset >= end.
        if off >= end:
            fb['raw'] = 'DtbOffset >= end'
            res.update(pass_=False, note='ABL: "Dtb offset goes beyond the image size" (0x12ab0); '
                       'BootLinux fails, the kernel is not started')
            return res
        rc = abl.call(RVA['fdt_check_header'], [IMG + ps + off]) & 0xffffffff
        rc = rc - (1 << 32) if rc & 0x80000000 else rc
        fb.update(raw_dtb_offset=ps + off, raw_fdt_check_header=rc)
        if rc == 0:
            res.update(pass_=False, note='no valid DTBO and no appended-DTB match: the ABL would '
                       'use the raw DTB at image+%#x with no overlay' % (ps + off))
        else:
            res.update(pass_=False, note='no valid DTBO; DeviceTreeAppended 0x140d8 found no match '
                       'and fdt_check_header(image+%#x) = %d, so the ABL prints "Error: Device Tree '
                       'blob not found" (0x12944) and BootLinux fails: the kernel is never started'
                       % (ps + off, rc))
        return res
    soc = abl.call(RVA['GetSocDtb'], [IMG, end, off, 0])
    res['soc_dtb'] = ('image+%#x' % (soc - IMG)) if soc else None
    if not soc:
        res.update(pass_=False, failed_at='GetSocDtb', note='Error: Appended Soc Device Tree blob not found')
        return res
    soc_size = struct.unpack('>I', abl.r(soc + 4, 4))[0]
    res['soc_props'] = fdt_root_props(abl.r(soc, soc_size))
    not_needed = abl.r(BASE + G_DTBO_NOT_NEEDED, 1)[0]
    res['dtbo_not_needed_flag'] = bool(not_needed)
    lst = abl.alloc(8)
    abl.w64(lst, 0)
    if not not_needed:
        board = abl.call(RVA['GetBoardDtb'], [info, abl.r64(bp + 0x18)])
        if not board:
            res.update(pass_=False, failed_at='GetBoardDtb', note='Error: Board Dtbo blob not found')
            return res
        bsize = struct.unpack('>I', abl.r(board + 4, 4))[0]
        res['board_dtbo'] = {'addr': board, 'bytes': bsize,
                             'props': fdt_root_props(abl.r(board, bsize))}
        if not abl.call(RVA['AppendToDtList'], [lst, board, bsize]) & 0xff:
            res.update(pass_=False, failed_at='AppendToDtList')
            return res
    if not abl.r64(lst):
        res.update(pass_=False, failed_at='overlay', note='ApplyOverlay: Overlay DT is NULL')
        return res
    if not abl.call(RVA['DtoArenaInit'], []):
        raise HarnessError('ufdt arena allocation failed')
    blob = abl.call(RVA['ufdt_install_blob'], [soc, soc_size])
    if not blob:
        res.update(pass_=False, failed_at='ufdt_install_blob')
        return res
    blob_size = struct.unpack('>I', abl.r(blob + 4, 4))[0]
    merged = abl.call(RVA['ufdt_apply_multioverlay'], [blob, blob_size, abl.r64(lst)], budget_s=120)
    abl.call(RVA['DeleteDtList'], [lst])
    if not merged:
        res.update(pass_=False, failed_at='ufdt_apply_multioverlay',
                   note='ApplyOverlay: ufdt apply overlay failed')
        return res
    msize = struct.unpack('>I', abl.r(merged + 4, 4))[0]
    data = abl.r(merged, msize)
    res['merged'] = {'bytes': msize, 'sha256': hashlib.sha256(data).hexdigest(),
                     'props': fdt_root_props(data)}
    abl.merged_dtb = data
    res['pass_'] = res['merged']['props'] is not None
    return res


_ABL_CACHE = {}


def load_abl(abl_path):
    if abl_path not in _ABL_CACHE:
        img, nrel = map_pe(extract_linuxloader(abl_path), BASE)
        _ABL_CACHE[abl_path] = (img, nrel, bl_targets(img))
    return _ABL_CACHE[abl_path]


def run(image_path, target, dtbo_path=DEFAULT_DTBO, abl_path=DEFAULT_ABL, part_size=None,
        discover=False, image=None, dtbo=None):
    """Run all stages. image/dtbo may be given as bytes (tests); otherwise
    they are read from image_path/dtbo_path."""
    img, nrel, entries = load_abl(abl_path)
    abl = Abl(img, entries, discover=discover)
    # BoardInfo fixture
    bi = BASE + G_BOARDINFO
    for o, v in [(0, BOARD['platform']), (4, BOARD['version']), (8, BOARD['subtype']),
                 (0x10, BOARD['chip_id']), (0x24, BOARD['chip_version']), (0x28, BOARD['foundry'])]:
        abl.w32(bi + o, v)
    if image is None:
        image = open(image_path, 'rb').read()
    psize = part_size or PART_SIZE[target]
    if len(image) > psize:
        raise HarnessError('image (%d bytes) is larger than the %s partition (%d)' % (len(image), target, psize))
    report = {'image': image_path, 'image_bytes': len(image),
              'image_sha256': hashlib.sha256(image).hexdigest(), 'target': target,
              'abl': abl_path, 'linuxloader_sha256': PE_SHA256, 'relocations': nrel,
              'partition_size': psize, 'stages': {}}
    stages = report['stages']
    report['host_checks'] = host_checks(image)
    abl.map_buffer(IMG, image, psize)
    if len(image) < 0x660:
        stages['header'] = {'pass': False, 'note': 'image smaller than a v1 header (ABL: '
                            '"Invalid boot image header size")'}
        return abl, report
    s1 = stage_header(abl, image, target, IMG)  # GetImage hands over the loaded buffer
    s1['log'] = abl.log[:]
    stages['header'] = s1
    if not s1['pass']:
        return abl, report
    hv = u32(image, 40)
    if hv > 2:
        stages['dtbo'] = {'pass': None, 'note': 'header v%d: vendor_boot path, not emulated' % hv}
        return abl, report
    dtbo_addr = dtbo_size = 0
    if target == 'boot':
        if dtbo is None:
            dtbo = open(dtbo_path, 'rb').read()
        dtbo_size = abl.map_buffer(DTBO, dtbo, DTBO_PART_SIZE)
        dtbo_addr = DTBO
        report['dtbo'] = dtbo_path
        report['dtbo_sha256'] = hashlib.sha256(dtbo).hexdigest()
    info = make_info(abl, target, hv, dtbo_addr, dtbo_size)
    # BootParamlist fields: +8 ImageBuffer, +0x10 ImageSize, +0x18 DtboImgBuffer,
    # +0x5c PageSize (LoadAndValidateDtboImg 0x23d78).
    bp = abl.alloc(0x200)
    abl.uc.mem_write(bp, b'\0' * 0x200)
    abl.w64(bp + 8, IMG)
    abl.w64(bp + 0x10, psize)
    abl.w32(bp + 0x5c, s1['page_size'])
    n = len(abl.log)
    s2 = stage_dtbo(abl, info, bp)
    s2['log'] = abl.log[n:]
    stages['dtbo'] = s2
    n = len(abl.log)
    s3 = stage_dtb(abl, image, info, bp, s2['pass'])
    s3['pass'] = s3.pop('pass_', None)
    s3['log'] = abl.log[n:]
    s3['pmic_queries'] = sorted(set(abl.pmic_queries))
    stages['dtb'] = s3
    return abl, report


def result_of(report):
    verdicts = [s.get('pass') for s in report['stages'].values()]
    ok = len(verdicts) == 3 and all(v is True for v in verdicts)
    return 'PASS' if ok and report.get('host_checks', {}).get('pass') else 'FAIL'


def failed_stage(report):
    for k in ('header', 'dtbo', 'dtb'):
        s = report['stages'].get(k)
        if s is None or s.get('pass') is not True:
            return k
    return None


def summarize(report):
    lines = ['abl-check: %s (%s, %d bytes, sha256 %s...)' % (
        report['image'], report['target'], report['image_bytes'], report['image_sha256'][:12])]
    names = {'header': '1 header   CheckImageHeader 0x132a0',
             'dtbo': '2 dtbo     LoadAndValidateDtboImg 0x23d78',
             'dtb': '3 dtb      GetSocDtb/GetBoardDtb/ufdt overlay'}
    hc = report.get('host_checks', {})
    lines.append('  %-48s %-7s %s' % ('0 file     sections inside the file (host)',
                                     'PASS' if hc.get('pass') else 'FAIL', hc.get('note', '')))
    for k in ('header', 'dtbo', 'dtb'):
        s = report['stages'].get(k)
        if s is None:
            lines.append('  %-48s SKIPPED (earlier stage failed)' % names[k])
            continue
        verdict = {True: 'PASS', False: 'FAIL', None: 'NOT RUN'}[s.get('pass')]
        extra = ''
        if k == 'header' and 'rc' in s:
            extra = 'rc=%#x %s, ImageSizeActual=%d, PageSize=%d' % (
                s['rc'], s['status'], s['image_size_actual'], s['page_size'])
        elif k == 'dtbo' and 'rc' in s:
            extra = 'rc=%d' % s['rc'] + (', %s, %d entr%s' % (
                s['source'], s['table_entries'], 'y' if s['table_entries'] == 1 else 'ies') if s['pass'] else '')
        elif k == 'dtb':
            m = s.get('merged')
            if m:
                extra = 'merged %d bytes, model "%s"' % (m['bytes'], (m['props'] or {}).get('model'))
            elif s.get('failed_at'):
                extra = 'failed at %s' % s['failed_at']
        lines.append('  %-48s %-7s %s' % (names[k], verdict, extra))
        if s.get('note'):
            lines.append('      note: %s' % s['note'])
        if s.get('pass') is False:
            log = s.get('log', [])
            important = [i for i, (lvl, _) in enumerate(log) if lvl in ('ERROR', 'UFDT')]
            context = set(important) | set(range(max(0, len(log) - 3), len(log)))
            for i in sorted(context):
                lines.append('      abl[%s] %s' % log[i])
    return '\n'.join(lines)


LIMITS = ('Not emulated: AVB/vbmeta verification (libavb), partition reads, the '
          'BootLinux glue that computes the DTB offsets (recomputed on the host from the '
          'disassembly), hypervisor DTBO '
          'overlays (none on this phone: "VM Hyp calls not present"), DT fixups and '
          'cmdline construction, kernel decompression and kernel boot. The PMIC '
          'revisions and BoardInfo are fixtures from the stock boot log. A PASS '
          'means the ABL code paths that rejected TWRP builds 1-2 accept the image; '
          'it does not prove that the kernel boots.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('image')
    ap.add_argument('target_pos', nargs='?', choices=['recovery', 'boot'], metavar='recovery|boot',
                    help='same as --target')
    ap.add_argument('--target', choices=['recovery', 'boot'])
    ap.add_argument('--dtbo', default=DEFAULT_DTBO, help='dtbo partition image (boot target)')
    ap.add_argument('--abl', default=DEFAULT_ABL, help='abl partition dump (must hold the pinned LinuxLoader)')
    ap.add_argument('--partition-size', type=int)
    ap.add_argument('--save-dtb', metavar='FILE', help='write the merged DTB the ABL produced (stage 3)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--quiet', action='store_true', help='verdict lines only')
    ap.add_argument('--discover', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if bool(a.target) == bool(a.target_pos) and a.target != a.target_pos:
        ap.error('give the target once: recovery or boot')
    a.target = a.target or a.target_pos
    try:
        abl, report = run(a.image, a.target, a.dtbo, a.abl, a.partition_size, a.discover)
    except (HarnessError, OSError) as e:
        print('abl-check: HARNESS ERROR: %s' % e, file=sys.stderr)
        return 2
    report['limits'] = LIMITS
    if a.save_dtb and abl.merged_dtb:
        with open(a.save_dtb, 'wb') as f:
            f.write(abl.merged_dtb)
    if a.discover:
        report['discovered'] = {'%#x' % k: 'first block %#x, LR %#x' % v for k, v in sorted(abl.discovered.items())}
    ok = result_of(report) == 'PASS'
    report['result'] = result_of(report)
    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(summarize(report) if not a.quiet else
              '\n'.join(l for l in summarize(report).splitlines() if not l.startswith('      ')))
        print('  RESULT: %s' % report['result'])
        if not a.quiet:
            print('  limits: ' + LIMITS)
        if a.discover:
            print('  discovered: ' + ' '.join(report['discovered']))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
