#!/usr/bin/env python3
"""Standalone reimplementation of the Hisense A9 software TCON (steady-state path).

Clean-room model of what the stock composer
(vendor.qti.hardware.display.composer-service, sha256 8c89557b...) does between
"a new 8-bit grey picture is ready" and "drive frames go to the DSI link":

  grey 1648x824  --conditioning (mode)-->  levels 0..15
  (old, new) levels  --pack-->  uint16 index per pixel (old<<4|new, or 256)
  index + waveform type + temperature bin  --lookUpTable-->  448x829 XRGB frames

It carries NO vendor data: the waveform tables, the bin map, the init flash and
the 32x32 dither matrix are read at runtime from the composer binary you pass in
(its sha256 is checked). Addresses and the algorithm are documented in
docs/mainline-plan.md section 4. Verified against the stock code running under
Unicorn by scripts/mainline/tcon-oracle.py.

Not modelled here: the multi-step mode-switch requests and the RGBA->grey +
rotation stage. Both are in the C port (scripts/mainline/tcon/, tested against
the same oracle by tcon/test-tcon-c.py). The DSI commit is modelled nowhere.

Usage as a library: t = Tcon(open(composer,'rb').read()); see tcon-oracle.py.
Run any code that reads vendor files with python3 -I.
"""
import hashlib

import numpy as np

COMPOSER_SHA256 = "8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed"

W, H = 1648, 824                 # native TCON buffer: 1648 wide, 824 rows
N = W * H
TW, TH = 448, 829                # transport frame: 448 x 829 words of 4 bytes
FRAME_BYTES = TW * TH * 4        # 0x16ab00
HEAD_ROWS = 5                    # rows 0..4 of every frame carry no data
DATA_WORDS = W // 4              # 412 words carry 4 pixels each; 412..447 are 0

# Waveform table classes, from the lookUpTable jump table at 0x2ad8c.
# base = first row's length byte; row = [len][code per frame...]
TABLES = {
    #  name      base      rowstride binstride slots
    "clear3":   (0x2b793,  0x6a, 0x6a6a, 257),   # types 3,4,5,7 and default
    "type2":    (0x8895f,  0x6a, 0x6a6a, 257),   # type 2 (clear flag on 1/3)
    "balanced": (0xe5b2b,  0x38, 0x3838, 256),   # type 1
    "smooth":   (0x116e3b, 0x1e, 0x1e1e, 256),   # types 6, 8
    "speed":    (0x1313df, 0x1e, 0x1e1e, 257),   # type 9
}
TYPE_CLASS = {1: "balanced", 2: "type2", 3: "clear3", 4: "clear3", 5: "clear3",
              6: "smooth", 7: "clear3", 8: "smooth", 9: "speed"}
BIN_MAP = 0x2b0d0                # 51 bytes, average temperature 0..50 C -> bin 0..13
INIT_TABLE, INIT_STRIDE = 0x2b103, 0x78   # global init flash, uniform frames
DITHER_MATRIX = 0x14b983         # 32x32 ordered-dither thresholds


class Tcon:
    def __init__(self, composer: bytes, controls=(30, 30, 0)):
        if hashlib.sha256(composer).hexdigest() != COMPOSER_SHA256:
            raise ValueError("unsupported composer binary (sha256 mismatch)")
        self.bin_ = composer
        self.bin_map = list(composer[BIN_MAP:BIN_MAP + 51])
        self.matrix = np.frombuffer(composer, np.uint8, 1024, DITHER_MATRIX).reshape(32, 32)
        self.set_controls(*controls)
        # persistent engine state (globals in the stock binary)
        self.counter = 0                         # 0x4c7fbc
        self.scratch = np.zeros(257, np.uint8)   # 0x4c86d9, slot 256 survives 256-slot loads
        self.cache = np.zeros(FRAME_BYTES, np.uint8)   # 0x35d4b0 via [0x4c7fb0]
        self.null = np.zeros(FRAME_BYTES, np.uint8)    # 0x1f29a8 via [0x35d4a8], memset 0
        self.init_done = False                   # 0x4c7fb8 bit 0
        self.balanced_full = 0                   # request S+4 (1 only on one transition path)

    # ---- grey conditioning tables (EpdSetContrast knobs; builders 0x1a48c8 / 0x1a49a4)
    def set_controls(self, black, white, contrast):
        b = black if 0 <= black <= 100 else 30
        h = 255 - (7 * white // 20) if 0 <= white <= 100 else 244
        m = 50 + 80 * (100 - contrast) // 100 if 0 <= contrast <= 100 else 130
        g2 = []
        for x in range(256):
            if b < x <= 200:
                q = (x - b) * m // (200 - b)
            elif 201 <= x < h:
                q = (x - 200) * (255 - m) // (h - 200) + m
            else:
                q = 0 if x < h else 255
            g2.append(q)
        c = contrast if 0 <= contrast <= 100 else 0
        g16 = [x if c == 0 else ((x * (200 - c) // 200 if x <= 200 else
                                  (x - 200) * (c + 55) // 55 + 200 - c) & 255)
               for x in range(256)]
        self.g2 = np.array(g2, np.uint8)
        self.g16 = np.array(g16, np.uint8)

    # ---- temperature -> bin (EinkSwTconThreadHandler 0x1a7a94-0x1a7b28)
    def temp_bin(self, t1, t2):
        def clamp(t):
            t &= 0xffffffff
            if t & 0x80:
                return 0
            t = t - (1 << 32) if t & 0x80000000 else t
            return t if t < 50 else 50
        s = 2 * clamp(t1) + clamp(t2)
        avg = 0 if s < 3 else (50 if s > 152 else int(s / 3))
        return self.bin_map[avg]

    # ---- dithers --------------------------------------------------------------
    @staticmethod
    def _errdiff_1bit(img):
        """Serpentine 1-bit error diffusion (0x1a741c-0x1a7640): 5/16 ahead, 7/16 below,
        1/8 below-diagonal alternating with column parity. Returns 0x00/0xff bytes."""
        work = [int(v) for v in img]
        out = bytearray(N)
        for y in range(H):
            rev = y & 1
            base = y * W
            last_row = y >= H - 1
            for scan in range(W):
                x = W - 1 - scan if rev else scan
                i = base + x
                p = work[i]
                e = p - 255 if p > 127 else p
                out[i] = 0xff if p > 127 else 0
                if scan < W - 1:
                    work[i - 1 if rev else i + 1] += (5 * e) >> 4
                if not last_row:
                    work[i + W] += (7 * e) >> 4
                    if scan < W - 1 and not scan & 1:
                        work[i + W + (-1 if rev else 1)] += e >> 3
                    elif scan & 1:
                        work[i + W + (1 if rev else -1)] += e >> 3
        return np.frombuffer(bytes(out), np.uint8)

    def _ordered_1bit(self, img):
        """32x32 ordered dither (0x1a764c / 0x1a78c8): white if p > M[y&31][x&31] or p > 0xef."""
        a = img.reshape(H, W)
        m = np.tile(self.matrix, (H // 32 + 1, W // 32 + 1))[:H, :W]
        return np.where((a > m) | (a > 0xef), 0xff, 0).astype(np.uint8).ravel()

    @staticmethod
    def _dither16(img):
        """DitherSStyle16 (0x1a8300): 16-level serpentine diffusion over 3 rows below,
        column phase x%3 (scan%3 on reverse rows). Returns level<<4 (or 0xff) bytes."""
        def tdiv(a, b):
            q = abs(a) // b
            return q if a >= 0 else -q
        work = [int(v) for v in img]
        out = bytearray(N)
        for y in range(H):
            rev = y & 1
            low = y <= H - 4          # rows y+1..y+3 exist (y <= 820)
            for scan in range(W):
                x = W - 1 - scan if rev else scan
                i = y * W + x
                p = work[i]
                q = tdiv(p, 16)
                r = p - q * 16
                if r <= 7:
                    o, e = q << 4, r
                elif p > 0xef:
                    o, e = 0xff, p - 255
                else:
                    o, e = (q + 1) << 4, p - 16 * (q + 1)
                e8, e38 = e >> 3, (3 * e) >> 3
                d, d2, h = tdiv(e8, 3), tdiv(e38, 3), tdiv(e8, 2)
                k = scan % 3
                f = -1 if rev else 1           # "ahead" direction
                if scan <= W - 2:
                    work[i + f] += e8 if k <= 1 else h
                if low:
                    work[i + W] += d
                    work[i + 3 * W] += d
                    work[i + 2 * W] += (h if (rev and k == 2) else d)
                    if scan <= W - 2 and not scan & 1:
                        work[i + W + f] += d2 + (h if k == 2 else 0)
                        work[i + 2 * W + f] += d2
                        work[i + 3 * W + f] += d2
                    elif scan & 1:
                        work[i + W - f] += d2
                        work[i + 2 * W - f] += d2
                        work[i + 3 * W - f] += d2
                out[i] = o & 0xff
        return np.frombuffer(bytes(out), np.uint8)

    # ---- conditioning (0x1a7338-0x1a79b8) --------------------------------------
    def condition(self, grey, mode):
        """grey: uint8[N] (row-major 1648x824). Returns target levels 0..15."""
        img = np.asarray(grey, np.uint8)
        lo, hi = mode & 0xff, (mode >> 8) & 0xff
        if lo in (1, 6):
            img = self.g2[img]
            img = self._ordered_1bit(img) if hi & 4 else self._errdiff_1bit(img)
        elif lo == 2:
            img = self._dither16(self.g16[img])
        elif lo == 3:
            img = self.g16[img]
            if hi & 1:
                img = self._dither16(img)
        elif lo == 9:
            img = self._ordered_1bit(self.g2[img])
        # 0, 4, 5, 7, 8: no conditioning
        return (img >> 4).astype(np.uint8)

    # ---- packing (0x1a7a00 plain, 0x1a7e64 with the "256" sentinel) -------------
    @staticmethod
    def pack(old, new, mode, clear):
        old = np.asarray(old, np.uint8)
        new = np.asarray(new, np.uint8)
        pairs = (old.astype(np.uint16) << 4) | new
        if (mode & 0xff) == 3 and not clear:
            o2, n2 = old.reshape(H, W), new.reshape(H, W)
            p2 = pairs.reshape(H, W)
            steady = (o2 == 15) & (n2 == 15)
            ys, xs = np.nonzero((o2 != 15) & (n2 == 15))
            for y, x in zip(ys.tolist(), xs.tolist()):   # row-major order, like the loop
                for yy, xx in ((y, x - 1), (y, x + 1), (y - 1, x), (y + 1, x)):
                    if 0 <= xx < W and 0 <= yy < H and steady[yy, xx]:
                        p2[yy, xx] = 256
                        break
        return pairs

    @staticmethod
    def final_type(mode, clear):
        t = mode & 0xff
        return 2 if (t | 2) == 3 and clear else t

    # ---- frame generation (lookUpTable 0x1d1690) --------------------------------
    def _row(self, cls, b, i, f):
        base, rs, bs, _ = TABLES[cls]
        return self.bin_[base + b * bs + i * rs + f]

    def _load(self, cls, b, frame):
        base, rs, bs, slots = TABLES[cls]
        start = base + b * bs + frame + 1
        self.scratch[:slots] = np.frombuffer(self.bin_, np.uint8, rs * slots, start)[::rs][:slots]

    def _render(self, pairs, out):
        lut = self.scratch
        v = lut[np.asarray(pairs).reshape(H, DATA_WORDS, 4)]
        byte0 = (v[..., 0] << 6) | (v[..., 1] << 4) | (v[..., 2] << 2) | v[..., 3]
        o = out.reshape(TH, TW, 4)
        o[:HEAD_ROWS, :, 0] = 0
        o[HEAD_ROWS:, :DATA_WORDS, 0] = byte0
        o[HEAD_ROWS:, DATA_WORDS:, 0] = 0

    def lookup(self, wtype, b, pairs, out):
        """One call of lookUpTable: fills out (uint8[FRAME_BYTES]); returns 1 = more frames."""
        cls = TYPE_CLASS.get(wtype, "clear3")
        base, rs, bs, slots = TABLES[cls]
        length = self.bin_[base + b * bs]
        c = self.counter
        hold = cls in ("smooth", "speed") or (cls == "balanced" and not self.balanced_full)
        if not hold:                                  # every frame from the table
            self._load(cls, b, c)
            self._render(pairs, out)
            c += 1
            self.counter = 0 if c >= length else c
            return int(c < length)
        if c == 0:                                    # first frame from the table, cached
            self._load(cls, b, 0)
            self._render(pairs, out)
            self.cache[:] = out
            self.counter = 1
            return 1
        last = length - 2 if cls == "balanced" else length - 1
        if 1 <= c < last:                             # hold: repeat the first frame
            out[:] = self.cache
        elif cls == "balanced" and c == last:         # Balanced emits two null frames
            out[:] = self.null
        else:                                         # final null frame, done
            out[:] = self.null
            self.counter = 0
            return 0
        self.counter = c + 1
        return 1

    def init_frame(self, b, out):
        """The one-time init flash after TCON start (0x1a7d04-0x1a7df0): uniform frames."""
        row = INIT_TABLE + b * INIT_STRIDE
        length = self.bin_[row]
        c = self.counter
        code = self.bin_[row + c + 1] & 3
        o = out.reshape(TH, TW, 4)
        o[:HEAD_ROWS, :, 0] = 0
        o[HEAD_ROWS:, :DATA_WORDS, 0] = code * 0x55
        o[HEAD_ROWS:, DATA_WORDS:, 0] = 0
        c += 1
        if c >= length:
            self.counter = 0
            self.init_done = True
            return 0
        self.counter = c
        return 1

    # ---- one steady update -----------------------------------------------------
    def update(self, old_levels, grey, mode, clear, t1, t2):
        """Yield the drive frames of one steady update (no mode-switch steps).
        Returns (via StopIteration.value) the new 'old' levels."""
        b = self.temp_bin(t1, t2)
        new = self.condition(grey, mode)
        pairs = self.pack(old_levels, new, mode, clear)
        t = self.final_type(mode, clear)
        out = np.zeros(FRAME_BYTES, np.uint8)
        while True:
            out[:] = 0
            more = self.lookup(t, b, pairs, out)
            yield out
            if not more:
                break
        return new
