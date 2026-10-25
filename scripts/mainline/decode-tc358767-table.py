#!/usr/bin/env python3
"""Decode the A9 kernel's TC358767 init table (`tc358767_cmd`) into register writes.

Usage: python3 -I decode-tc358767-table.py <vmlinux.elf>

<vmlinux.elf> is the vmlinux-to-elf output of the device's own boot_a kernel
(4.19.157-perf, Dec 28 2022). The table is read from the ELF at run time; this
script carries no vendor data, only mainline register names
(drivers/gpu/drm/bridge/tc358767.c) and the loop facts measured in
dsi2dpi_init (6-byte i2c_msg per entry, 25 entries, usleep_range(1000,1100)
after entries 3 and 5). Needs pyelftools (and kern-dis.py next to it).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module  # noqa: E402

kd = import_module("kern-dis")

NAMES = {
    0x0104: "PPI_STARTPPI", 0x0114: "PPI_LPTXTIMECNT", 0x0134: "PPI_LANEENABLE",
    0x013c: "PPI_TX_RX_TA", 0x0164: "PPI_D0S_CLRSIPOCOUNT", 0x0168: "PPI_D1S_CLRSIPOCOUNT",
    0x016c: "PPI_D2S_CLRSIPOCOUNT", 0x0170: "PPI_D3S_CLRSIPOCOUNT", 0x0204: "DSI_STARTDSI",
    0x0210: "DSI_LANEENABLE", 0x0440: "DPIPXLFMT", 0x0448: "POCTRL", 0x0450: "VPCTRL0",
    0x0454: "HTIM01", 0x0458: "HTIM02", 0x045c: "VTIM01", 0x0460: "VTIM02", 0x0464: "VFUEN0",
    0x0510: "SYSCTRL", 0x06a0: "DP0_SRCCTRL", 0x0800: "DP_PHY_CTRL", 0x0900: "DP0_PLLCTRL",
    0x0904: "DP1_PLLCTRL", 0x0908: "PXL_PLLCTRL", 0x0914: "PXL_PLLPARAM", 0x0918: "SYS_PLLPARAM",
}
SLEEP_AFTER = {3, 5}   # dsi2dpi_init: cmp w26,#5 / cmp w26,#3 -> usleep_range(1000, 1100)


def fields(reg, v):
    if reg == 0x0448:
        return " ".join(n for b, n in ((7, "S2P"), (3, "PCLK_POL"), (2, "VS_POL"), (1, "HS_POL"), (0, "DE_POL")) if v >> b & 1)
    if reg == 0x0450:
        return f"VSDELAY={v >> 20 & 0xfff} OPXLFMT={'RGB888' if v >> 8 & 1 else 'RGB666'} FRMSYNC={v >> 4 & 1} MSF={v & 1}"
    if reg == 0x0454:
        return f"HPW={v & 0x1ff} HBPR={v >> 16 & 0x1ff}"
    if reg == 0x045c:
        return f"VSPR={v & 0xff} VBPR={v >> 16 & 0xff}"
    if reg == 0x0914:
        return (f"VCO_HI={v >> 24 & 1} EXT_PRE_DIV={v >> 20 & 0xf} EXT_POST_DIV={v >> 16 & 0xf} "
                f"IN_SEL(bits15:14)={v >> 14 & 3} DIV={v >> 8 & 0xf} MUL={v & 0x7f}")
    if reg == 0x0918:
        return f"REF_FREQ={['38.4M', '19.2M', '26M', '13M'][v >> 8 & 3]} SYSCLK_SEL={v >> 4 & 3} LSCLK_DIV={v & 3}"
    if reg in (0x0900, 0x0904, 0x0908):
        return " ".join(n for b, n in ((2, "PLLUPDATE"), (1, "PLLBYP"), (0, "PLLEN")) if v >> b & 1) or "off"
    if reg == 0x0800:
        return " ".join(n for b, n in ((28, "DP_PHY_RST"), (25, "BGREN"), (24, "PWR_SW_EN"), (12, "PHY_M1_RST"),
                                       (8, "PHY_M0_RST"), (2, "PHY_2LANE"), (1, "PHY_A0_EN"), (0, "PHY_M0_EN")) if v >> b & 1)
    if reg == 0x06a0:
        return " ".join(n for b, n in ((13, "SCRMBLDIS"), (12, "EN810B"), (7, "LANESKEW"), (3, "SSCG"), (2, "LANES_2"),
                                       (1, "BW27"), (0, "AUTOCORRECT")) if v >> b & 1)
    if reg in (0x0134, 0x0210):
        return " ".join(n for b, n in ((0, "CLEN"), (1, "L0EN"), (2, "L1EN"), (3, "L2EN"), (4, "L3EN")) if v >> b & 1)
    if reg == 0x0510:
        return ["VIDSRC_NONE", "VIDSRC_DSI_RX", "VIDSRC_DPI_RX", "VIDSRC_COLOR_BAR"][v & 3]
    if reg == 0x013c:
        return f"TTA_GET={v >> 16:#x} TTA_SURE={v & 0xffff}"
    return ""


def main():
    k = kd.Kern(sys.argv[1])
    va = k.byname["tc358767_cmd"][0]
    n = k.size(va) // 6
    raw = k.read(va, n * 6)
    print(f"# tc358767_cmd @ {va:#x}, {n} entries x 6 bytes (reg u16 BE, value u32 LE), i2c addr 0x0f")
    print("| # | reg | name (mainline) | value | decoded |")
    print("|---|---|---|---|---|")
    for i in range(n):
        reg = struct.unpack(">H", raw[6 * i:6 * i + 2])[0]
        val = struct.unpack("<I", raw[6 * i + 2:6 * i + 6])[0]
        print(f"| {i} | 0x{reg:04x} | {NAMES.get(reg, '?')} | 0x{val:08x} | {fields(reg, val)} |")
        if i in SLEEP_AFTER:
            print(f"| | | *usleep_range(1000, 1100)* | | |")


if __name__ == "__main__":
    main()
